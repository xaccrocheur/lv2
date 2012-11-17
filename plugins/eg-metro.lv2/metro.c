/*
  LV2 Metronome Example Plugin
  Copyright 2012 David Robillard <d@drobilla.net>

  Permission to use, copy, modify, and/or distribute this software for any
  purpose with or without fee is hereby granted, provided that the above
  copyright notice and this permission notice appear in all copies.

  THIS SOFTWARE IS PROVIDED "AS IS" AND THE AUTHOR DISCLAIMS ALL WARRANTIES
  WITH REGARD TO THIS SOFTWARE INCLUDING ALL IMPLIED WARRANTIES OF
  MERCHANTABILITY AND FITNESS. IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR
  ANY SPECIAL, DIRECT, INDIRECT, OR CONSEQUENTIAL DAMAGES OR ANY DAMAGES
  WHATSOEVER RESULTING FROM LOSS OF USE, DATA OR PROFITS, WHETHER IN AN
  ACTION OF CONTRACT, NEGLIGENCE OR OTHER TORTIOUS ACTION, ARISING OUT OF
  OR IN CONNECTION WITH THE USE OR PERFORMANCE OF THIS SOFTWARE.
*/

/**
   @file metro.c Metronome Plugin
*/

#include <math.h>
#include <stdbool.h>
#include <stdlib.h>
#include <string.h>

#include <stdio.h>

#include "lv2/lv2plug.in/ns/ext/atom/atom.h"
#include "lv2/lv2plug.in/ns/ext/atom/util.h"
#include "lv2/lv2plug.in/ns/ext/time/time.h"
#include "lv2/lv2plug.in/ns/ext/urid/urid.h"
#include "lv2/lv2plug.in/ns/lv2core/lv2.h"

#ifndef M_PI
#    define M_PI 3.14159265
#endif

#define EG_METRO_URI "http://lv2plug.in/plugins/eg-metro"

typedef struct {
	LV2_URID atom_Blank;
	LV2_URID atom_Float;
	LV2_URID atom_Path;
	LV2_URID atom_Resource;
	LV2_URID time_Position;
	LV2_URID time_barBeat;
	LV2_URID time_beatsPerMinute;
	LV2_URID time_speed;
} MetroURIs;

static const double attack_s = 0.005;
static const double decay_s  = 0.075;

enum {
	METRO_CONTROL = 0,
	METRO_NOTIFY  = 1,
	METRO_OUT     = 2
};

typedef enum {
	STATE_ATTACK,
	STATE_DECAY,
	STATE_OFF
} State;

typedef struct {
	/* Features */
	LV2_URID_Map* map;

	/* URIs */
	MetroURIs uris;

	/* Ports */
	struct {
		LV2_Atom_Sequence* control;
		LV2_Atom_Sequence* notify;
		float*             output;
	} ports;

	double   rate;
	float    bpm;
	float    speed;
	uint32_t elapsed_len;  /**< Frames since last click start */
	uint32_t wave_offset;  /**< Current play offset in wave */
	float*   wave;         /**< One cycle of a sine wave */
	uint32_t wave_len;     /**< Length of wave in frames */
	uint32_t attack_len;   /**< Attack duration in frames */
	uint32_t decay_len;    /**< Decay duration in frames */
	State    state;        /**< Play state */
} Metro;

static void
connect_port(LV2_Handle instance,
             uint32_t   port,
             void*      data)
{
	Metro* self = (Metro*)instance;

	switch (port) {
	case METRO_CONTROL:
		self->ports.control = (LV2_Atom_Sequence*)data;
		break;
	case METRO_NOTIFY:
		self->ports.notify = (LV2_Atom_Sequence*)data;
		break;
	case METRO_OUT:
		self->ports.output = (float*)data;
		break;
	default:
		break;
	}
}

static void
activate(LV2_Handle instance)
{
	Metro* self = (Metro*)instance;

	self->elapsed_len = 0;
	self->wave_offset = 0;
	self->state       = STATE_OFF;
}

static LV2_Handle
instantiate(const LV2_Descriptor*     descriptor,
            double                    rate,
            const char*               path,
            const LV2_Feature* const* features)
{
	Metro* self = (Metro*)calloc(1, sizeof(Metro));
	if (!self) {
		return NULL;
	}

	/* Scan host features for URID map */
	LV2_URID_Map* map = NULL;
	for (int i = 0; features[i]; ++i) {
		if (!strcmp(features[i]->URI, LV2_URID_URI "#map")) {
			map = (LV2_URID_Map*)features[i]->data;
		}
	}
	if (!map) {
		fprintf(stderr, "Host does not support urid:map.\n");
		free(self);
		return NULL;
	}

	/* Map URIS */
	MetroURIs* const uris = &self->uris;
	self->map = map;
	uris->atom_Blank          = map->map(map->handle, LV2_ATOM__Blank);
	uris->atom_Float          = map->map(map->handle, LV2_ATOM__Float);
	uris->atom_Path           = map->map(map->handle, LV2_ATOM__Path);
	uris->atom_Resource       = map->map(map->handle, LV2_ATOM__Resource);
	uris->time_Position       = map->map(map->handle, LV2_TIME__Position);
	uris->time_barBeat        = map->map(map->handle, LV2_TIME__barBeat);
	uris->time_beatsPerMinute = map->map(map->handle, LV2_TIME__beatsPerMinute);
	uris->time_speed          = map->map(map->handle, LV2_TIME__speed);

	/* Initialise fields */
	self->rate       = rate;
	self->bpm        = 120.0f;
	self->attack_len = attack_s * rate;
	self->decay_len  = decay_s * rate;
	self->state      = STATE_OFF;

	/* Generate one cycle of a sine wave at the desired frequency. */
	const double freq = 440.0 * 2.0;
	const double amp  = 0.5;
	self->wave_len = rate / freq;
	self->wave     = (float*)malloc(self->wave_len * sizeof(float));
	for (uint32_t i = 0; i < self->wave_len; ++i) {
		self->wave[i] = sin(i * 2 * M_PI * freq / rate) * amp;
	}

	return (LV2_Handle)self;
}

static void
cleanup(LV2_Handle instance)
{
	free(instance);
}

static void
play(Metro* self, uint32_t begin, uint32_t end)
{
	float* const   output          = self->ports.output;
	const uint32_t frames_per_beat = 60.0f / self->bpm * self->rate;

	if (self->speed == 0.0f) {
		memset(output, 0, (end - begin) * sizeof(float));
		return;
	}

	for (uint32_t i = begin; i < end; ++i) {
		switch (self->state) {
		case STATE_ATTACK:
			/* Amplitude increases from 0..1 until attack_len */
			output[i] = self->wave[self->wave_offset] *
				self->elapsed_len / (float)self->attack_len;
			if (self->elapsed_len >= self->attack_len) {
				self->state = STATE_DECAY;
			}
			break;
		case STATE_DECAY:
			/* Amplitude decreases from 1..0 until attack_len + decay_len */
			output[i] = 0.0f;
			output[i] = self->wave[self->wave_offset] *
				(1 - ((self->elapsed_len - self->attack_len) /
				      (float)self->decay_len));
			if (self->elapsed_len >= self->attack_len + self->decay_len) {
				self->state = STATE_OFF;
			}
			break;
		case STATE_OFF:
			output[i] = 0.0f;
		}

		/* We continuously play the sine wave regardless of envelope */
		self->wave_offset = (self->wave_offset + 1) % self->wave_len;

		/* Update elapsed time and start attack if necessary */
		if (++self->elapsed_len == frames_per_beat) {
			self->state       = STATE_ATTACK;
			self->elapsed_len = 0;
		}
	}
}

static void
update_position(Metro* self, const LV2_Atom_Object* obj)
{
	const MetroURIs* uris = &self->uris;

	/* Received new transport position/speed */
	LV2_Atom *beat = NULL, *bpm = NULL, *speed = NULL;
	lv2_atom_object_get(obj,
	                    uris->time_barBeat, &beat,
	                    uris->time_beatsPerMinute, &bpm,
	                    uris->time_speed, &speed,
	                    NULL);
	if (bpm && bpm->type == uris->atom_Float) {
		/* Tempo changed, update BPM */
		self->bpm = ((LV2_Atom_Float*)bpm)->body;
	}
	if (speed && speed->type == uris->atom_Float) {
		/* Speed changed, e.g. 0 (stop) to 1 (play) */
		self->speed = ((LV2_Atom_Float*)speed)->body;
	}
	if (beat && beat->type == uris->atom_Float) {
		/* Received a beat position, synchronize.
		   This is a simple hard sync that may cause clicks.
		   A real plugin would do something more graceful.
		*/
		const float frames_per_beat = 60.0f / self->bpm * self->rate;
		const float bar_beats       = ((LV2_Atom_Float*)beat)->body;
		const float beat_beats      = bar_beats - floorf(bar_beats);
		self->elapsed_len           = beat_beats * frames_per_beat;
		if (self->elapsed_len < self->attack_len) {
			self->state = STATE_ATTACK;
		} else if (self->elapsed_len < self->attack_len + self->decay_len) {
			self->state = STATE_DECAY;
		} else {
			self->state = STATE_OFF;
		}
	}
}

static void
run(LV2_Handle instance, uint32_t sample_count)
{
	Metro*           self = (Metro*)instance;
	const MetroURIs* uris = &self->uris;

	/* Work forwards in time frame by frame, handling events as we go */
	const LV2_Atom_Sequence* in     = self->ports.control;
	uint32_t                 last_t = 0;
	for (LV2_Atom_Event* ev = lv2_atom_sequence_begin(&in->body);
	     !lv2_atom_sequence_is_end(&in->body, in->atom.size, ev);
	     ev = lv2_atom_sequence_next(ev)) {

		/* Play the click for the time slice from last_t until now */
		play(self, last_t, ev->time.frames);

		if (ev->body.type == uris->atom_Blank) {
			const LV2_Atom_Object* obj = (LV2_Atom_Object*)&ev->body;
			if (obj->body.otype == uris->time_Position) {
				/* Received position information, update */
				update_position(self, obj);
			}
		}

		/* Update time for next iteration and move to next event*/
		last_t = ev->time.frames;
		ev = lv2_atom_sequence_next(ev);
	}

	/* Play for remainder of cycle */
	play(self, last_t, sample_count);
}

static const LV2_Descriptor descriptor = {
	EG_METRO_URI,
	instantiate,
	connect_port,
	activate,
	run,
	NULL,  // deactivate,
	cleanup,
	NULL,  // extension_data
};

LV2_SYMBOL_EXPORT const LV2_Descriptor*
lv2_descriptor(uint32_t index)
{
	switch (index) {
	case 0:
		return &descriptor;
	default:
		return NULL;
	}
}