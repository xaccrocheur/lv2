#!/usr/bin/env python
import datetime
import glob
import os
import rdflib
import shutil
import subprocess
import sys

from waflib.extras import autowaf as autowaf
import waflib.Logs as Logs
import waflib.Options as Options
import waflib.Scripting as Scripting

# Version of this package (even if built as a child)
LV2EXT_VERSION = datetime.date.isoformat(datetime.datetime.now()).replace('-', '.')

# Variables for 'waf dist'
APPNAME = 'lv2world'
VERSION = LV2EXT_VERSION

# Mandatory variables
top = '.'
out = 'build'

def options(opt):
    opt.load('compiler_cc')
    opt.load('compiler_cxx')
    autowaf.set_options(opt)
    opt.add_option('--test', action='store_true', default=False, dest='build_tests',
                   help="Build unit tests")
    opt.add_option('--experimental', action='store_true', default=False,
                   dest='experimental',
                   help='Install unreleased experimental extensions')
    for i in ['lv2/lv2plug.in/ns/lv2core']:
        opt.recurse(i)

def get_subdirs(with_plugins=True):
    subdirs = ['lv2/lv2plug.in/ns/lv2core/']
    subdirs += glob.glob('lv2/lv2plug.in/ns/ext/*/')
    subdirs += glob.glob('lv2/lv2plug.in/ns/extensions/*/')
    if with_plugins:
        subdirs += glob.glob('plugins/*/')
    return subdirs
    
def configure(conf):
    conf.load('compiler_cc')
    conf.load('compiler_cxx')
    autowaf.configure(conf)
    autowaf.set_recursive()

    conf.env.append_unique('CFLAGS', '-std=c99')

    subdirs = get_subdirs()

    for i in subdirs:
        conf.recurse(i)

    conf.env['LV2_SUBDIRS'] = subdirs

# Rule for copying a file to the build directory
def copy(task):
    shutil.copy(task.inputs[0].abspath(), task.outputs[0].abspath())

def chop_lv2_prefix(s):
    if s.startswith('lv2/lv2plug.in/'):
        return s[len('lv2/lv2plug.in/'):]
    return s

# Rule for calling lv2specgen on a spec bundle
def specgen(task):
    import rdflib
    doap = rdflib.Namespace('http://usefulinc.com/ns/doap#')
    lv2  = rdflib.Namespace('http://lv2plug.in/ns/lv2core#')
    owl  = rdflib.Namespace('http://www.w3.org/2002/07/owl#')
    rdf  = rdflib.Namespace('http://www.w3.org/1999/02/22-rdf-syntax-ns#')

    sys.path.append("./lv2specgen")
    import lv2specgen

    spec   = task.inputs[0]
    path   = os.path.dirname(spec.srcpath())
    indir  = os.path.dirname(spec.abspath())
    outdir = os.path.abspath(os.path.join(out, chop_lv2_prefix(path)))

    bundle = str(outdir)
    b = os.path.basename(outdir)

    if not os.access(spec.abspath(), os.R_OK):
        print('warning: extension %s has no %s.ttl file' % (root, root))
        return

    try:
        model = rdflib.ConjunctiveGraph()
        for i in glob.glob('%s/*.ttl' % bundle):
            model.parse(i, format='n3')
    except:
        e = sys.exc_info()[1]
        print('error parsing %s: %s' % (bundle, str(e)))
        return

    # Get extension URI
    ext_node = model.value(None, rdf.type, lv2.Specification)
    if not ext_node:
        print('no extension found in %s' % bundle)
        return
    
    ext = str(ext_node)

    # Get version
    minor = 0
    micro = 0
    try:
        minor = int(model.value(ext_node, lv2.minorVersion, None))
        micro = int(model.value(ext_node, lv2.microVersion, None))
    except Exception as e:
        print("warning: %s: failed to find version for %s" % (bundle, ext))

    # Get date
    date = None
    for r in model.triples([ext_node, doap.release, None]):
        revision = model.value(r[2], doap.revision, None)
        if revision == ("%d.%d" % (minor, micro)):
            date = model.value(r[2], doap.created, None)
            break

    # Verify that this date is the latest
    for r in model.triples([ext_node, doap.release, None]):
        revision = model.value(r[2], doap.revision, None)
        this_date = model.value(r[2], doap.created, None)
        if this_date > date:
            print("warning: revision %d.%d (%s) is not the latest release" % (
                minor, micro, date))
            break
    
    # Get short description
    shortdesc = model.value(ext_node, doap.shortdesc, None)

    SPECGENDIR = 'lv2specgen'
    STYLEPATH  = 'build/aux/style.css'
    TAGFILE    = 'build/tags'

    specdoc = lv2specgen.specgen(
        spec.abspath(),
        SPECGENDIR,
        os.path.relpath(STYLEPATH, bundle),
        os.path.relpath('build/doc/html', bundle),
        TAGFILE,
        instances=True)

    lv2specgen.save(task.outputs[0].abspath(), specdoc)

    # Name (comment is to act as a sort key)
    row = '<tr><!-- %s --><td><a rel="rdfs:seeAlso" href="%s">%s</a></td>' % (
        b, path[len('lv2/lv2plug.in/ns/'):], b)

    # Description
    if shortdesc:
        row += '<td>' + str(shortdesc) + '</td>'
    else:
        row += '<td></td>'

    # Version
    version_str = '%s.%s' % (minor, micro)
    if minor == 0 or (micro % 2 != 0):
        row += '<td><span style="color: red">' + version_str + '</span></td>'
    else:
        row += '<td>' + version_str + '</td>'

    # Date
    row += '<td>%s</td>' % (str(date) if date else '')

    # Status
    deprecated = model.value(ext_node, owl.deprecated, None)
    if minor == 0:
        row += '<td><span class="error">Experimental</span></td>'
    elif deprecated and str(deprecated[2]) != "false":
        row += '<td><span class="warning">Deprecated</span></td>'
    elif micro % 2 == 0:
        row += '<td><span class="success">Stable</span></td>'

    row += '</tr>'

    index = open(os.path.join('build', 'index_rows', b), 'w')
    index.write(row)
    index.close()

def subst_file(template, output, dict):
    i = open(template, 'r')
    o = open(output, 'w')
    for line in i:
        for key in dict:
            line = line.replace(key, dict[key])
        o.write(line)
    i.close()
    o.close()

# Task to build extension index
def build_index(task):
    global index_lines
    rows = []
    for f in task.inputs:
        if not f.abspath().endswith('index.html.in'):
            rowfile = open(f.abspath(), 'r')
            rows += rowfile.readlines()
            rowfile.close()

    subst_file(task.inputs[0].abspath(), task.outputs[0].abspath(),
               { '@ROWS@': ''.join(rows),
                 '@TIME@': datetime.datetime.utcnow().strftime('%F %H:%M UTC') })

def build(bld):
    for i in bld.env['LV2_SUBDIRS']:
        bld.recurse(i)

    if bld.env['DOCS']:
        # Build Doxygen documentation (and tags file)
        autowaf.build_dox(bld, 'LV2', VERSION, top, out)

        # Copy stylesheet to build directory
        obj = bld(rule     = copy,
                  name     = 'copy',
                  source   = 'doc/style.css',
                  target   = 'aux/style.css')

        index_files = []

        # Generate .htaccess files (and directory skeleton)
        for i in bld.env['LV2_SUBDIRS']:
            if i.startswith('lv2/lv2plug.in'):
                # Copy spec files to build dir
                for f in bld.path.ant_glob(i + '*.*'):
                    obj = bld(rule   = copy,
                              name   = 'copy',
                              source = f,
                              target = chop_lv2_prefix(f.srcpath()))

                base = i[len('lv2/lv2plug.in'):]
                name = os.path.basename(i[:len(i)-1])
                index_file = os.path.join('index_rows', name)
                index_files += [index_file]

                # Generate .htaccess file
                obj = bld(features     = 'subst',
                          source       = 'doc/htaccess.in',
                          target       = os.path.join(base, '.htaccess'),
                          install_path = None,
                          NAME         = name,
                          BASE         = base)

                bld.add_group()

                # Call lv2specgen to generate spec docs
                obj  = bld(rule   = specgen,
                           name   = 'specgen',
                           source = os.path.join(i, name + '.ttl'),
                           target = ['%s%s.html' % (chop_lv2_prefix(i), name),
                                     index_file])

        index_files.sort()

        # Build extension index
        obj = bld(rule   = build_index,
                  name   = 'index',
                  source = ['lv2/lv2plug.in/ns/index.html.in'] + index_files,
                  target = 'ns/index.html')

def release(ctx):
    lv2  = rdflib.Namespace('http://lv2plug.in/ns/lv2core#')
    rdf  = rdflib.Namespace('http://www.w3.org/1999/02/22-rdf-syntax-ns#')
    doap = rdflib.Namespace('http://usefulinc.com/ns/doap#')

    try:
        shutil.rmtree('build/spec')
    except:
        pass

    os.makedirs('build/spec')

    manifests = glob.glob('lv2/lv2plug.in/ns/lv2core/manifest.ttl')
    manifests += glob.glob('lv2/lv2plug.in/ns/*/*/manifest.ttl')
    for manifest in manifests:
        dir = os.path.dirname(manifest)
        name = os.path.basename(dir).replace('.lv2', '')

        m = rdflib.ConjunctiveGraph()
        m.parse(manifest, format='n3')

        uri = minor = micro = None
        try:
            spec  = m.value(None, rdf.type, lv2.Specification)
            uri   = str(spec)
            minor = int(m.value(spec, lv2.minorVersion, None))
            micro = int(m.value(spec, lv2.microVersion, None))
        except:
            e = sys.exc_info()[1]
            Logs.error('error: %s: %s' % (manifest, str(e)))
            continue

        if minor != 0 and micro % 2 == 0:
            autowaf.display_header('\nBuilding %s Release\n' % dir)
            try:
                subprocess.call(
                    ['./waf', 'distclean', 'configure', 'build', 'distcheck'],
                    cwd=dir)
                for i in glob.glob(dir + '/*.tar.bz2'):
                    shutil.move(i, 'build/spec')
            except:
                Logs.error('Error building %s release' % (name, e))

            subprocess.call(['./waf', 'distclean'], cwd=dir)

def news(ctx):
    ctx.recurse(get_subdirs(False))

def dist(ctx):
    ctx.recurse(get_subdirs(False), name='pre_dist')
    ctx.archive()
    ctx.recurse(get_subdirs(False), name='post_dist')

def lint(ctx):
    for i in (['lv2/lv2plug.in/ns/lv2core/lv2.h']
              + glob.glob('lv2/lv2plug.in/ns/ext/*/*.h')
              + glob.glob('lv2/lv2plug.in/ns/extensions/*/*.h')):
        subprocess.call('cpplint.py --filter=+whitespace/comments,-whitespace/tab,-whitespace/braces,-whitespace/labels,-whitespace/blank_line,-build/header_guard,-readability/casting,-readability/todo,-build/include ' + i, shell=True)
