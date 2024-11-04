# Copyright 2024 Google LLC

# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at

#     https://www.apache.org/licenses/LICENSE-2.0

# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

#!/usr/bin/env python3
import base64
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
import json
import html as html_mod
import zlib
import diff as diff_mod
from svg import Svg


class Page(object):
  def __init__(self, name, page, safediffs, conflicts,
               datadir, proj, uidtable, symbols):
    self.pgFile = page
    self.safediffs = safediffs or []
    self.conflicts = conflicts or []
    # Make sure safediffs is the right format (match conflicts)
    if safediffs and not isinstance(safediffs[0], tuple):
      safediffs = [(d, None) for d in safediffs]
    # Generate the SVG for the page
    self.svg = Svg(header=False, autoAnimate=False)
    self.svg.symbols = symbols
    self.svg.uidtable = uidtable
    self.svg.datadir = datadir
    if proj:
      proj.fillsvg(self.svg, page)
    self.id = self.svg.vars.get('~pageid', 'page%s' % self.svg.getuid(self))
    self.name = ': '.join(
        (n for n in (name, self.svg.vars.get('~pagetitle')) if n))
    page.fillsvg(self.svg, diff_mod.targetdict(safediffs + conflicts))
    # Clear out symbol library; this is tracked elsewhere
    self.svg.symbols = {}
  def alldiffs(self):
    for diffpair in self.conflicts + self.safediffs:
      for diffs in diffpair:
        for diff in diffs:
          yield diff


class HTTPHandler(BaseHTTPRequestHandler):
  def do_POST(self):
    if not self.server.diffui.post(self):
      self.send_error(404)
  def do_GET(self):
    if not self.server.diffui.get(self):
      self.send_error(404)
  def log_message(self, fmt, *args):
    self.server.diffui.log(fmt % args)


class DiffUI(object):
  """Creates a diff/merge UI and launches it for the user, then updates the base
  with the user-selected diffs.
  """
  MODE_VIEW = 1
  MODE_DIFF = 2
  MODE_MERGE = 3
  MODE_ICONS = (None, 'md-memory', 'md-compare', 'md-merge')

  def __init__(self, title='webschviewdiffmerge', proj=None,
               mode=MODE_MERGE, verbosity=0):
    self.title = title
    self._proj = proj
    self._pages = []
    self._html = []
    self._mode = mode
    self._tempdir = None
    self._symbols = {}
    self._uidtable = []
    self._uiprocess = None
    self._response = None
    self._verbosity = verbosity
    self.schematic_index = {
      'nets':{},
      'comps':{},
      'pages':[],
      'diffs':{},
      'text': {},
      'pins': {}
    }

  def __del__(self):
    self._cleanup()

  def _mkdtemp(self):
    if not self._tempdir:
      self._tempdir = tempfile.mkdtemp(prefix='webschviewdiffmerge.diffui.%s' % id(self))
      with open(os.path.join(self._tempdir, 'First Run'), 'w'):
        pass

  def _cleanup(self):
    if self._uiprocess:
      try:
        self._uiprocess.terminate()
      except OSError:
        pass
    if self._tempdir:
      shutil.rmtree(self._tempdir, ignore_errors=True)
      self._tempdir = None


  def _update_index(self):
    for i in range(len(self._webschviewdiffmerges)):
      ui_page = self._pages[i]
      page_name = html_mod.escape(ui_page.pgFile.page_title('NO_TITLE'))
      self.schematic_index['pages'].append((ui_page.id, page_name, ui_page.svg.get_viewbox()))

      page_components = ui_page.pgFile.get_components()

      for c in page_components:
        for inst in page_components[c]:
          self.schematic_index['comps'].setdefault(
              c, []).append([i] + inst)

      page_nets = ui_page.pgFile.get_nets(include_power=True)
      for n in page_nets:
        self.schematic_index['nets'].setdefault(n, []).append(i)

      for s in ui_page.conflicts:
        self.schematic_index['diffs'].setdefault(ui_page.id, []).append([[{
          "text": str(s3).partition(': ')[2],
          "id": s3.svgclass(),
          "c": 1
        } for s3 in s2] for s2 in s])

      for s in ui_page.safediffs:
        self.schematic_index['diffs'].setdefault(ui_page.id, []).append([[{
          "text": str(s3).partition(': ')[2],
          "id": s3.svgclass()
        } for s3 in s2] for s2 in s])

      for text in ui_page.svg.generic_text:
        self.schematic_index['text'].setdefault(text , []).append(i)

      for pin in ui_page.svg.pin_text:
        self.schematic_index['pins'].setdefault(pin[1] , []).append([i, pin[0]])

  def addpage(self, name, page, safediffs, conflicts, datadir=None):
    p = Page(name, page, safediffs, conflicts,
      datadir, self._proj, self._uidtable, self._symbols)
    self._pages.append(p)
    return p

  def post(self, request):
    if request.path == '/apply':
      request.send_response(204)
      request.end_headers()

      self._response = []
      for line in request.rfile:
        self._response += json.loads(line.decode())

      # We have our response; shut down the server
      # Do it from another thread to avoid deadlock
      threading.Thread(target=request.server.shutdown).start()
      return True
    if request.path == '/openurl':
      request.send_response(204)
      request.end_headers()

      response = {}
      for line in request.rfile:
        response.update(json.loads(line.decode()))
      if response.get('url', '').startswith('https://'):
        chrome, shell = self._get_chrome_cmd()
        subprocess.Popen(chrome + (response['url'],), shell=shell)
      return True
    return False

  def get(self, request):
    if request.path != '/':
      return False
    datatype = 'text/html'
    data = '\n'.join(self.genhtml())
    request.send_response(200)
    request.send_header('Content-type', datatype)
    request.end_headers()
    request.wfile.write(data.encode())
    return True

  def log(self, msg):
    if self._verbosity > 0:
      sys.stderr.write('%s\n' % msg)

  def _icon(self, icon):
    icon = os.path.join(os.path.dirname(__file__), 'icons', icon)
    imagetype = ('svg+xml' if icon.endswith('.svg')
                 else icon.rpartition('.')[2].lower())
    return 'data:image/%s;base64,%s' % (imagetype,
          base64.b64encode(open(icon, 'rb').read()).decode('ascii'))

  def _fillsvg(self, svg):
    svg.symbols = self._symbols

  @staticmethod
  def _compress(s):
    data = zlib.compress(s.encode(), level=9)
    # Pad to 6 bytes with nulls (gzip doesn't care)
    data += b'\0' * ((6 - len(data) % 6) % 6)
    # Made-up base116 format; stores 6 bytes in 7.
    # Codex includes a bunch of control characters that seem to be accepted in
    # strings by both Chrome and Firefox. Slash is not included since it's
    # possible </script> could get generated and break the world.
    code = (b'0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz!"#$%&()*+,-.:;<=>?@[]^_`{|}~ '
            b'\007\010\011\013\014\016\017\020\021\022\023\024\025\026\027\030\031\032\033\034\035\036\037\177')
    POW = [116, 116**2, 116**3, 116**4, 116**5, 116**6]
    output = bytearray()
    for i in range(0, len(data), 6):
      num = int.from_bytes(data[i:i+6], byteorder='big')
      output.append(code[num // POW[5]      ])
      output.append(code[num // POW[4] % 116])
      output.append(code[num // POW[3] % 116])
      output.append(code[num // POW[2] % 116])
      output.append(code[num // POW[1] % 116])
      output.append(code[num // POW[0] % 116])
      output.append(code[num           % 116])
    return output.decode('ascii')

  def genhtml(self):
    self._update_index()

    html = ['<!DOCTYPE html>']
    html.append('<html lang="en"><head>')
    html.append(
        '<meta http-equiv="Content-Type" content="text/html; charset=utf-8">')
    html.append('<link rel="icon" href="%s"/>' %
                self._icon('%s.png' % DiffUI.MODE_ICONS[self._mode]))
    html.append('<title>%s</title></head>' % Svg.escape(self.title))
    html.append('<body>')
    srcdir = os.path.realpath(os.path.join(os.getcwd(), os.path.dirname(__file__)))
    # Embed styles
    html.append('<style>')
    for css in ['diffui.css', 'js-libraries/mdl.min.css']:
      html.append(open(os.path.join(srcdir, css), 'r', encoding='utf-8').read())
    html.append('</style>')
    # Embed js libraries
    html.append('<script>')
    for lib in ['hammer.min.js','mdl.min.js','pako_inflate.min.js','svg-pan-zoom.min.js']:
      html.append(open(os.path.join(srcdir, 'js-libraries/' + lib), 'r', encoding='utf-8').read())
    html.append('</script>')
    # Controls
    html.append(open(os.path.join(srcdir, 'diffui.html'), 'r', encoding='utf-8').read())
    # Code
    html.append('<script>')
    html.append('let schematicTitle = `%s`;' % self.title)
    html.append('let uiMode = %u;' % self._mode)
    if self._mode >= DiffUI.MODE_DIFF:
      html.append("let diffIcon = '%s';" %
          self._icon('%s.svg' % DiffUI.MODE_ICONS[self._mode]))
    html.append(open(os.path.join(srcdir, 'diffui.js'), 'r', encoding='utf-8').read())
    html.append('</script>')
    # Data
    html.append('<script>')
    html.append("var data = '%s';" %
        self._compress(json.dumps(self.schematic_index, sort_keys=True)))
    if self._pages:
      html.append('var pageData = {')
      lib = Svg(header=False, autoAnimate=False)
      lib.symbols = self._symbols
      html.append("library: '%s'," % self._compress(repr(lib)))
      for page in self._pages:
        html.append("%s: '%s'," % (page.id, self._compress(repr(page.svg))))
      html.append('};')
    html.append('</script>')
    html.append('</body></html>')
    return html

  @staticmethod
  def _get_chrome_cmd():
    if sys.platform.startswith(('win32', 'cygwin')):
      return ('start', '/wait', '/b', 'chrome'), True
    elif sys.platform.startswith('darwin'):
      return ('open', '-a', 'Google Chrome', '-n', '-W', '--args'), False
    # For the Linux case, check for Chromium if Chrome doesn't exist
    for cmd in ('google-chrome', 'chromium-browser', 'chromium'):
      try:
        if subprocess.call((cmd, '--version'), stdout=subprocess.DEVNULL) == 0:
          return (cmd,), False
      except FileNotFoundError:
        pass
    raise FileNotFoundError('neither Google Chrome nor Chromium could be found')

  def _launch_ui(self, server):
    try:
      chrome, shell = self._get_chrome_cmd()
      self._uiprocess = subprocess.Popen(chrome + (
          '--user-data-dir=%s' % self._tempdir, '--renderer-process-limit=1',
          '--disable-extensions', '--no-proxy-server',
          '--disable-component-extensions-with-background-pages',
          '--app=http://localhost:%u' % server.server_address[1]),
        shell=shell,
        stderr=subprocess.STDOUT,
        stdout=open(os.path.join(self._tempdir, 'chrome.log'), 'wb')
        )
    except FileNotFoundError as e:
      sys.stderr.write('ERROR: %s\n' % e)
      server.shutdown()
      return 127
    ret = self._uiprocess.wait()
    server.shutdown()
    return ret

  def getresponse(self):
    self._response = None
    self._mkdtemp()
    server = HTTPServer(('localhost', 0), HTTPHandler)
    server.diffui = self
    uithread = threading.Thread(target=self._launch_ui, args=(server,))
    uithread.start()
    server.serve_forever()
    self._cleanup()
    if self._response is None:
      return None
    idmap = {}
    for page in self._pages:
      idmap.update({d.svgclass(): d for d in page.alldiffs()})
    return [idmap[d] for d in idmap if d not in self._response]

def main(argv):
  import time
  from filetypes import pgFile as pgFile_mod
  if len(argv) == 1 or argv[1] in ('-h', '--help'):
    sys.stderr.write('''Usage:
  %s [-1|-2|-3|-4] base [ours [theirs [output]]] ...
  Shows one or more pages, diffs or 3-way merges.
  If one pgFile is provided, or -1 is specfied, just renders the pages.
  If two pgFiles are provided, or -2 is specified, renders an interactive diff for
      each pair of pgFile files.
  If three pgFiles are provided, or -3 is specified, renders an interactive merge
      for each set of three pgFile files (base, ours, theirs). Upon submitting,
      the resulting pgFile files are written to stdout.
  If four pgFiles are provided, or -4 is specified, behaves the same as -3 but
      writes the resulting pgFile files to the fourth filename in the set.
  ''')
    return 2*(len(argv) == 1)
  if argv[1].startswith('-'):
    count = int(argv[1][1:])
    paths = argv[2:]
  else:
    count = len(argv)-1
    paths = argv[1:]
  if count < 1 or count > 4 or len(paths) % count:
    sys.stderr.write('Invalid argument count\n')
    return 2
  ui = DiffUI(mode=count)
  base_pgFiles = []
  for i in range(0, len(paths), count):
    pgFiles = [pgFile_mod.pgFile(open(p, 'rb')) for p in paths[i:i+min(count,3)]]
    base_pgFiles.append(pgFiles[0])
    conflicts = []
    diffs = []
    if count >= 3:
      conflicts = diff_mod.threeway(
          pgFiles[0], pgFiles[1], pgFiles[2], return_safe=diffs)
    elif count == 2:
      for difftree in pgFiles[0].diff(pgFiles[1]):
        diffs += [([d], []) for d in difftree._flatten()]
    ui.addpage(paths[i], pgFiles[0], diffs, conflicts)
  selected = ui.getresponse()
  if count < 3:
    return 0
  if selected is None:
    return 2
  if diff_mod.applylists(selected):
    raise Exception('conflicting diffs selected')
  for i in range(len(paths)//count):
    base_pgFiles[i].set_timestamp(time.strftime('%c'))
    base_pgFiles[i].write(open(paths[i*4+3], 'wb') if count == 4 else sys.stdout.buffer)
if __name__ == '__main__':
  sys.exit(main(sys.argv))