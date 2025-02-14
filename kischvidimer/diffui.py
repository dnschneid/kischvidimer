# SPDX-FileCopyrightText: (C) 2025 Rivos Inc.
# SPDX-FileCopyrightText: Copyright 2024 Google LLC
#   Licensed under the Apache License, Version 2.0 (the "License");
#   you may not use this file except in compliance with the License.
#   You may obtain a copy of the License at
#
#       http://www.apache.org/licenses/LICENSE-2.0
#
#   Unless required by applicable law or agreed to in writing, software
#   distributed under the License is distributed on an "AS IS" BASIS,
#   WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#   See the License for the specific language governing permissions and
#   limitations under the License.
# SPDX-License-Identifier: Apache-2.0

import base64
import contextlib
import gc
import html as html_mod
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import zlib
from http.server import BaseHTTPRequestHandler, HTTPServer
from io import BytesIO

from . import diff as diff_mod
from . import git, themes
from .kicad_common import Drawable, Variables
from .svg import Svg


class Page:
  def __init__(
    self,
    name,
    page,
    instances,
    safediffs,
    conflicts,
    proj,
    variables,
    uidtable,
    symbols,
    worksheet,
  ):
    self.sch = page
    self.title = page.title or ""
    self.instances = instances
    if variables and instances:
      self.title = variables.expand(instances[0], self.title)
    self.safediffs = safediffs or []
    self.conflicts = conflicts or []
    # Make sure safediffs is the right format (match conflicts)
    if safediffs and not isinstance(safediffs[0], tuple):
      safediffs = [(d, None) for d in safediffs]
    diffs = diff_mod.targetdict(safediffs + conflicts)
    # Generate the SVG for the page
    self.svg = Svg(header=False, auto_animate=False)
    self.svg.symbols = symbols
    self.svg.uidtable = uidtable
    self.svg.worksheet = worksheet
    self.svg.metadata_context = None
    variables = variables or Variables()
    context = variables.context()
    if proj:
      context += proj.context()
    else:
      page.fillvars(variables, diffs, context)
    self.id = self.svg.vars.get("~pageid", f"page{self.svg.getuid(self)}")
    self.name = ": ".join(
      n for n in (name, self.svg.vars.get("~pagetitle")) if n
    )
    # Common background elements
    page.fillsvg(
      self.svg, diffs, Drawable.DRAW_STAGE_COMMON_BG, context=context
    )
    # Page-specific elements
    for path, sheet in instances:
      uuid = path.uuid(sheet)
      self.svg.metadata_context = uuid
      pgcontext = context + (path, sheet)
      self.svg.gstart(hidden=[(True, None), (False, f"instance {uuid}")])
      page.fillsvg(
        self.svg, diffs, Drawable.DRAW_STAGE_PAGE_SPECIFIC, context=pgcontext
      )
      self.svg.gend()
    self.svg.metadata_context = None
    # Common foreground elements
    page.fillsvg(
      self.svg, diffs, Drawable.DRAW_STAGE_COMMON_FG, context=context
    )
    # Clear out symbol library; this is tracked elsewhere
    self.svg.symbols = {}

  def alldiffs(self):
    for diffpair in self.conflicts + self.safediffs:
      for diffs in diffpair:
        yield from diffs


class HTTPHandler(BaseHTTPRequestHandler):
  def do_POST(self):  # noqa: N802
    if not self.server.diffui.post(self):
      self.send_error(404)

  def do_GET(self):  # noqa: N802
    if not self.server.diffui.get(self):
      self.send_error(404)

  def log_message(self, fmt, *args):
    self.server.diffui.log(fmt % args)


class DiffUI:
  """Creates a diff/merge UI and launches it for the user, then updates the base
  with the user-selected diffs.
  """

  MODE_VIEW = 1
  MODE_DIFF = 2
  MODE_MERGE = 3
  MODE_ICONS = (None, "memory", "compare", "merge")

  def __init__(
    self,
    title=None,
    ver=None,
    proj=None,
    worksheet=None,
    variables=None,
    mode=MODE_MERGE,
    verbosity=0,
  ):
    gc.disable()
    self.title = title
    self.ver = ver or ""
    self._proj = proj
    self._variables = variables
    self._pages = []
    self._pagemap = {}  # id(sch) to _pages index
    self._toc = None
    self._html = []
    self._mode = mode
    self._tempdir = None
    self._symbols = {}
    self._uidtable = []
    self._uiprocess = None
    self._response = None
    self._worksheet = worksheet
    self._verbosity = verbosity
    self.schematic_index = None

  def __del__(self):
    self._cleanup()
    gc.enable()

  def _mkdtemp(self):
    if not self._tempdir:
      self._tempdir = tempfile.mkdtemp(prefix=f"kischvidimer.diffui.{id(self)}")
      with open(os.path.join(self._tempdir, "First Run"), "w"):
        pass

  def _cleanup(self):
    if self._uiprocess:
      with contextlib.suppress(OSError):
        self._uiprocess.terminate()
    if self._tempdir:
      shutil.rmtree(self._tempdir, ignore_errors=True)
      self._tempdir = None

  def _update_index(self):
    self.schematic_index = {
      "nets": {},
      "comps": {},
      "pages": [],
      "diffs": {},
      "text": {},
      "pins": {},
    }
    # Apply TOC
    toc_page_map = []

    def add_to_index(toc, depth=0):
      for inst in toc:
        ui_page = self._pages[self._pagemap[id(inst["sch"])]]
        toc_page_map.append(ui_page)
        page_name = html_mod.escape(inst["name"])
        self.schematic_index["pages"].append(
          {
            "id": ui_page.id,
            "pn": inst["page"],
            "inst": inst["uuid"],
            "name": page_name,
            "depth": depth,
            "box": tuple(map(int, ui_page.svg.get_viewbox(with_wks=True))),
            "contentbox": tuple(
              map(int, ui_page.svg.get_viewbox(with_wks=False))
            ),
          }
        )
        add_to_index(inst.get("children", []), depth + 1)

    if self._toc is None:
      self.generate_toc()
    add_to_index(self._toc)

    for i, (ui_page, page) in enumerate(
      zip(toc_page_map, self.schematic_index["pages"])
    ):
      instance = page["inst"]
      page_components = ui_page.sch.get_components(
        instance, variables=self._variables
      )

      for c in page_components:
        for inst in page_components[c]:
          inst[chr(0)] = i  # store page index in 0-prop
          self.schematic_index["comps"].setdefault(c, []).append(inst)

      page_nets = ui_page.sch.get_nets(
        instance, variables=self._variables, include_power=True
      )
      for n in page_nets:
        self.schematic_index["nets"].setdefault(n, []).append(i)

      for s in ui_page.conflicts:
        self.schematic_index["diffs"].setdefault(ui_page.id, []).append(
          [
            [
              {"text": str(s3).partition(": ")[2], "id": s3.svgclass(), "c": 1}
              for s3 in s2
            ]
            for s2 in s
          ]
        )

      for s in ui_page.safediffs:
        self.schematic_index["diffs"].setdefault(ui_page.id, []).append(
          [
            [
              {"text": str(s3).partition(": ")[2], "id": s3.svgclass()}
              for s3 in s2
            ]
            for s2 in s
          ]
        )

      for uuid, text in ui_page.svg.generic_text:
        if uuid in (None, instance):
          self.schematic_index["text"].setdefault(text, []).append(i)

      for uuid, pin_num, pin_name in ui_page.svg.pin_text:
        if uuid in (None, instance):
          self.schematic_index["pins"].setdefault(pin_name, []).append(
            [i, pin_num]
          )

  def addpage(self, name, page, instances, safediffs, conflicts):
    p = Page(
      name,
      page,
      instances,
      safediffs,
      conflicts,
      self._proj,
      self._variables,
      self._uidtable,
      self._symbols,
      self._worksheet,
    )
    self._pagemap[id(page)] = len(self._pages)
    self._pages.append(p)
    return p

  def set_toc(self, toc):
    # toc is the same as kicad_pro.gen_toc, except once we implement diffs, inst
    # contents will be arrays
    self._toc = toc

  def generate_toc(self):
    # generates a fake table of contents with every instance of every page
    self._toc = []
    for num, page in enumerate(self._pages):
      parent = None
      for sub_num, (path, sheet) in enumerate(page.instances):
        uuid = path.uuid(sheet)
        inst = {
          "page": num + 1,
          "name": f"{page.name} ({sub_num})",
          "uuid": uuid,
          "file": page.sch._fname,
          "sch": page.sch,
        }
        if parent:
          parent.setdefault("children", []).append(inst)
        else:
          parent = inst
      self._toc.append(parent)

  def post(self, request):
    if request.path == "/apply":
      request.send_response(204)
      request.end_headers()

      self._response = []
      for line in request.rfile:
        self._response += json.loads(line.decode())

      # We have our response; shut down the server
      # Do it from another thread to avoid deadlock
      threading.Thread(target=request.server.shutdown).start()
      return True
    if request.path == "/openurl":
      request.send_response(204)
      request.end_headers()

      response = {}
      for line in request.rfile:
        response.update(json.loads(line.decode()))
      if response.get("url", "").startswith(("http://", "https://")):
        chrome, shell = self._get_chrome_cmd()
        subprocess.Popen(chrome + (response["url"],), shell=shell)
      return True
    return False

  def get(self, request):
    if request.path != "/":
      return False
    datatype = "text/html"
    data = "\n".join(self.genhtml(is_app=True))
    request.send_response(200)
    request.send_header("Content-type", datatype)
    request.end_headers()
    request.wfile.write(data.encode())
    trigger_gc()
    return True

  def log(self, msg):
    if self._verbosity > 0:
      sys.stderr.write(f"{msg}\n")

  def _icon(self, icon):
    icon = os.path.join(os.path.dirname(__file__), "icons", icon)
    imagetype = (
      "svg+xml" if icon.endswith(".svg") else icon.rpartition(".")[2].lower()
    )
    b64 = base64.b64encode(open(icon, "rb").read()).decode("ascii")
    return f"data:image/{imagetype};base64,{b64}"

  def _genfont(self, font, name):
    glyphs = set()
    for page in self._pages:
      glyphs.update(page.svg.glyphs)
    src = font
    if ord(max(glyphs)) < 0x2500:
      src = f"{font}-latin"
    font = os.path.join(os.path.dirname(__file__), "fonts", f"{src}.woff")
    # Use fontTools if installed to only include used glyphs
    try:
      import fontTools.subset as fts

      subsetter = fts.Subsetter()
      subsetter.populate(text="".join(glyphs))
      options = fts.Options()
      fontdata = BytesIO()
      with fts.load_font(font, options, dontLoadGlyphNames=True) as srcfont:
        subsetter.subset(srcfont)
        srcfont.save(fontdata)
      fontdata = fontdata.getvalue()
    except ImportError:
      fontdata = open(font, "rb").read()
    b64 = base64.b64encode(fontdata).decode("ascii")
    return f"""@font-face {{
      font-family: '{name}';
      src: url(data:application/x-font-woff;charset=utf-8;base64,{b64});
      font-weight: normal;
      font-style: normal;
      }}"""

  def _fillsvg(self, svg):
    svg.symbols = self._symbols

  @staticmethod
  def _compress(s):
    data = zlib.compress(s.encode(), level=9)
    # Pad to 6 bytes with nulls (gzip doesn't care)
    data += b"\0" * ((6 - len(data) % 6) % 6)
    # Made-up base116 format; stores 6 bytes in 7.
    # Codex includes a bunch of control characters that seem to be accepted in
    # strings by both Chrome and Firefox. Slash is not included since it's
    # possible </script> could get generated and break the world.
    code = (
      b"0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
      b'!"#$%&()*+,-.:;<=>?@[]^_`{|}~ '
      b"\007\010\011\013\014\016\017\020\021\022\023\024\025\026\027"
      b"\030\031\032\033\034\035\036\037\177"
    )
    output = bytearray()
    for i in range(0, len(data), 6):
      num = int.from_bytes(data[i : i + 6], byteorder="big")
      output.append(code[num // 116**6])
      output.append(code[num // 116**5 % 116])
      output.append(code[num // 116**4 % 116])
      output.append(code[num // 116**3 % 116])
      output.append(code[num // 116**2 % 116])
      output.append(code[num // 116**1 % 116])
      output.append(code[num % 116])
    return output.decode("ascii")

  @staticmethod
  def loadhtml(path):
    # Loads in an html file and replaces <img> tags with embedded SVG files
    with open(path, encoding="utf-8") as f:
      h = f.read()

    def attrs(tag, overrides=None):
      data = {a[1]: a[2] for a in re.finditer(r'\b([a-zA-Z]+)="([^"]+)"', tag)}
      if overrides:
        data.update(overrides)
      return data

    def repl(m):
      data = attrs(m[0])
      if "src" not in data:
        return m[0]
      # Load the SVG
      with open(os.path.join(os.path.dirname(path), data["src"])) as f:
        s = f.read()
      # Remove "fill" from the icon if not specified in the img tag
      data.setdefault("fill", None)
      data["src"] = None
      # Merge the SVG tag
      inner = "".join(f' {k}="{v}"' for k, v in attrs(m[0], data).items() if v)
      return re.sub(r"<svg\s[^>]*>", lambda m: f"<svg{inner}>", s)

    return re.sub(r"<img\s[^>]*>", repl, h)

  def genhtml(self, is_app=False):
    self._update_index()

    # Generate title
    title = [
      ["kischvidimer", "kischVIdimer", "kischviDImer", "kischvidiMER"][
        min(self._mode, DiffUI.MODE_MERGE)
      ]
    ] * is_app
    if self._pages:
      firstpageid = self.schematic_index["pages"][0]["id"]
      for p in self._pages:
        if p.id == firstpageid:
          title.append(p.title)
          break
    if self.title:
      title.append(self.title)
    window_title = ": ".join(title)
    if self.ver:
      window_title += f" - {self.ver}"

    html = ["<!DOCTYPE html>"]
    html.append("""<!--
    Various licenses apply to portions of this file as indicated below.
    The embedded schematic may be proprietary to its author and all rights are
    reserved to that author unless expressly stated otherwise in the rendered
    schematic.\n-->""")

    html.append('<html lang="en"><head>')
    html.append(
      '<meta http-equiv="Content-Type" content="text/html; charset=utf-8">'
    )
    pageicon = self._icon(f"{DiffUI.MODE_ICONS[self._mode]}.svg")
    html.append(f'<link rel="icon" href="{pageicon}"/>')
    html.append(f"<title>{Svg.escape(window_title)}</title></head>")
    html.append("<body>")
    srcdir = os.path.realpath(
      os.path.join(os.getcwd(), os.path.dirname(__file__))
    )
    # Embed styles
    html.append("<style>")
    for css in ["diffui.css", "js-libraries/material.min.css"]:
      with open(os.path.join(srcdir, css), encoding="utf-8") as f:
        html.extend(
          line.strip() for line in f if "sourceMappingURL" not in line
        )
    html.append("</style>")
    # Embed js libraries
    html.append("<script>")
    jsdir = os.path.join(srcdir, "js-libraries")
    for lib in os.listdir(jsdir):
      if lib.endswith(".min.js"):
        with open(os.path.join(jsdir, lib), encoding="utf-8") as f:
          html.extend(
            line.strip() for line in f if "sourceMappingURL" not in line
          )
    html.append("</script>")
    # Controls
    html.append(DiffUI.loadhtml(os.path.join(srcdir, "diffui.html")))
    # Code
    html.append("<script>")
    html.append(f'let uiVersion = "{git.get_version(srcdir)}";')
    safetitle = title[-1].replace("\\", "\\\\").replace('"', '\\"')
    safevers = self.ver.replace("\\", "\\\\").replace('"', '\\"')
    html.append(f'let schematicTitle = "{safetitle}";')
    html.append(f'let schematicVersion = "{safevers}";')
    html.append(f"let uiMode = {self._mode};")
    if self._mode >= DiffUI.MODE_DIFF:
      difficon = self._icon(f"{DiffUI.MODE_ICONS[self._mode]}.svg")
      html.append(f"let diffIcon = '{difficon}';")
    fburl = ""
    if self._variables:
      fburl = self._variables.resolve(self._variables.GLOBAL, "feedbackURL")
    html.append(f"let feedbackURL = '{fburl or ''}';")
    html.append(f"let themeDefault = '{themes.themes()[0][0]}';")
    bwtheme = next(
      t[0]
      for t in themes.themes()
      if "black" in t[0].lower() and "white" in t[0].lower()
    )
    html.append(f"let themeBW = '{bwtheme}';")
    html.append(f"let themes = {json.dumps(themes.todict())};")
    html.append(
      open(os.path.join(srcdir, "diffui.js"), encoding="utf-8").read()
    )
    html.append("</script>")
    # KiCad font (added late to speed up display of the loading dialog)
    html.append("<style>")
    html.append(self._genfont("newstroke", "kicad"))
    html.append("</style>")
    # Data
    html.append("<script>")
    html.append("""/*
    The following encoded schematic may be proprietary to its author and all
    rights are reserved to that author unless expressly stated otherwise in the
    rendered schematic.\n*/""")
    zindex = self._compress(json.dumps(self.schematic_index, sort_keys=True))
    html.append(f"var data = '{zindex}';")
    if self._pages:
      html.append("var pageData = {")
      lib = Svg(header=False, auto_animate=False)
      lib.symbols = self._symbols
      html.append(f"library: '{self._compress(repr(lib))}',")
      for page in self._pages:
        html.append(f"{page.id}: '{self._compress(repr(page.svg))}',")
      html.append("};")
    html.append("</script>")
    html.append("</body></html>")
    return html

  @staticmethod
  def _get_chrome_cmd():
    if sys.platform.startswith(("win32", "cygwin")):
      return ("start", "/wait", "/b", "chrome"), True
    elif sys.platform.startswith("darwin"):
      return ("open", "-a", "Google Chrome", "-n", "-W", "--args"), False
    # For the Linux case, check for Chromium if Chrome doesn't exist
    for cmd in ("google-chrome", "chromium-browser", "chromium"):
      try:
        if subprocess.call((cmd, "--version"), stdout=subprocess.DEVNULL) == 0:
          return (cmd,), False
      except FileNotFoundError:
        pass
    raise FileNotFoundError("neither Google Chrome nor Chromium could be found")

  def _launch_ui(self, server):
    try:
      chrome, shell = self._get_chrome_cmd()
      self._uiprocess = subprocess.Popen(
        chrome
        + (
          f"--user-data-dir={self._tempdir}",
          "--renderer-process-limit=1",
          "--disable-extensions",
          "--no-proxy-server",
          "--disable-component-extensions-with-background-pages",
          f"--app=http://localhost:{server.server_address[1]}",
        ),
        shell=shell,
        stderr=subprocess.STDOUT,
        stdout=open(os.path.join(self._tempdir, "chrome.log"), "wb"),
      )
    except FileNotFoundError as e:
      sys.stderr.write(f"ERROR: {e}\n")
      server.shutdown()
      return 127
    ret = self._uiprocess.wait()
    server.shutdown()
    return ret

  def getresponse(self):
    self._response = None
    self._mkdtemp()
    server = HTTPServer(("localhost", 0), HTTPHandler)
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


def trigger_gc():
  def dogc():
    time.sleep(1)
    gc.collect()
    gc.enable()

  gcthread = threading.Thread(target=dogc)
  gcthread.start()


def main(argv):
  from . import kicad_sch as sch_mod

  if len(argv) == 1 or argv[1] in ("-h", "--help"):
    sys.stderr.write("""Usage:
  %s [-1|-2|-3|-4] base [ours [theirs [output]]] ...
  Shows one or more pages, diffs or 3-way merges.
  If one kicad_sch is provided, or -1 is specfied, just renders the pages.
  If two kicad_schs are provided, or -2 is specified, renders an interactive
    diff for each pair of kicad_sch files.
  If three kicad_schs are provided, or -3 is specified, renders an interactive
    merge for each set of three kicad_sch files (base, ours, theirs). Upon
    submitting, the resulting kicad_sch files are written to stdout.
  If four kicad_schs are provided, or -4 is specified, behaves the same as -3
    but writes the resulting kicad_sch files to the fourth filename in the set.
  """)
    return 2 * (len(argv) == 1)
  if argv[1].startswith("-"):
    count = int(argv[1][1:])
    paths = argv[2:]
  else:
    count = len(argv) - 1
    paths = argv[1:]
  if count < 1 or count > 4 or len(paths) % count:
    sys.stderr.write("Invalid argument count\n")
    return 2
  ui = DiffUI(mode=count)
  base_schs = []
  for i in range(0, len(paths), count):
    schs = [
      sch_mod.kicad_sch(open(p, "rb"), p) for p in paths[i : i + min(count, 3)]
    ]
    instances = [s.inferred_instances() for s in schs]
    base_schs.append(schs[0])
    conflicts = []
    diffs = []
    if count >= 3:
      conflicts = diff_mod.threeway(
        schs[0], schs[1], schs[2], return_safe=diffs
      )
    elif count == 2:
      for difftree in schs[0].diff(schs[1]):
        diffs += [([d], []) for d in difftree._flatten()]
    ui.addpage(paths[i], schs[0], instances[0], diffs, conflicts)
  selected = ui.getresponse()
  if count < 3:
    return 0
  if selected is None:
    return 2
  if diff_mod.applylists(selected):
    raise Exception("conflicting diffs selected")
  for i in range(len(paths) // count):
    base_schs[i].set_timestamp(time.strftime("%c"))
    base_schs[i].write(
      open(paths[i * 4 + 3], "wb") if count == 4 else sys.stdout.buffer
    )


if __name__ == "__main__":
  sys.exit(main(sys.argv))
