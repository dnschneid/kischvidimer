# SPDX-FileCopyrightText: (C) 2025 Rivos Inc.
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

import datetime
import json
import os
import sys

from . import git, kicad_sch, kicad_wks, progress, sexp
from .diff import Comparable
from .kicad_common import variant_context

DEFAULT_LICENSE_HEADER = (
  "Proprietary to its author and all rights are reserved to that "
  "author unless expressly stated otherwise in the rendered schematic"
)


class KicadPro(Comparable):
  """Kicad project file"""

  def __init__(self, f, fname=None, data_filter_func=None):
    self._fname = fname
    data = f.read()
    if data_filter_func is not None:
      data = data_filter_func(data if isinstance(data, str) else data.decode())
    self.json = json.loads(data)
    self.variant = ""  # active design variant; "" is the base design
    self._sheet_insts = {}  # page instance uuid -> (path, sheet)
    self._sheet_flag_cache = {}

  @property
  def project(self):
    return self.json["meta"]["filename"].replace(".kicad_pro", "")

  @property
  def variables(self):
    return self.json.get("text_variables", {})

  def get_variable(self, name, default=None):
    """Returns a project text variable, matching the name case-insensitively."""
    for v in self.variables:
      if v.lower() == name.lower():
        return self.variables[v]
    return default

  @property
  def variants(self):
    """Returns a dict of variant names defined in the project to descriptions.
    Schematics can reference additional variants; see variant_names."""
    variants = self.json.get("schematic", {}).get("variants") or []
    return {
      v["name"]: v.get("description", "") for v in variants if "name" in v
    }

  @property
  def default_variant(self):
    """The variant to render unless overridden, from the DEFAULT_VARIANT
    project text variable. None if not specified."""
    return self.get_variable("DEFAULT_VARIANT")

  @property
  def show_dnp_markers(self):
    drawing = self.json.get("schematic", {}).get("drawing", {})
    return drawing.get("show_dnp_markers", True)

  def variant_names(self, pages):
    """Returns the set of all known variant names, from the project and the
    schematic pages. Mirrors SCHEMATIC::LoadVariants."""
    names = set(self.variants)
    for _instances, sch in pages.values():
      names.update(sch.variant_names())
    return names

  def context(self):
    s = sexp.SExp.init([sexp.Atom("~project"), self.project])
    s.kicad_pro = self
    return (s,) + variant_context(self.variant)

  def sheet_flag(self, flag, uuid, context):
    """Returns True if the flag is set on any sheet in the hierarchy of the
    page instance uuid, for the active variant. Ignores diffs."""
    key = (flag, uuid, self.variant)
    if key not in self._sheet_flag_cache:
      result = False
      parts = uuid.split("/")
      # Skip the root page, which doesn't have a sheet
      for i in range(3, len(parts) + 1):
        inst = self._sheet_insts.get("/".join(parts[:i]))
        if inst is None:
          continue
        path, sheet = inst
        if sheet.flag(flag, None, self.context() + (path,)).v:
          result = True
          break
      self._sheet_flag_cache[key] = result
    return self._sheet_flag_cache[key]

  def fillnetlist(self, netlister, diffs, pages=None, p=None):
    if not pages:
      return
    context = self.context()
    # Collect bus aliases so that all pages can use them
    for _filename, (_instances, sch) in pages.items():
      for ba in sch.getsubs("bus_alias"):
        context[0].add(ba)
    # Starting with v10, bus aliases are stored in the kicad_pro
    pro_aliases = self.json.get("schematic", {}).get("bus_aliases", {})
    for name, members in pro_aliases.items():
      context[0].add(
        sexp.SExp(
          [
            sexp.Atom("bus_alias"),
            name,
            sexp.SExp([sexp.Atom("members")] + members),
          ]
        )
      )
    for filename, (instances, sch) in pages.items():
      if p:
        p.set_text(f"Netlisting {filename[:-10]}").write().incr()
      for path, sheet in instances:
        netlister.netprefix = self.uuid_to_name(pages, path.uuid(sheet))
        assert netlister.netprefix
        pgcontext = context + (path, sheet)
        sch.fillnetlist(netlister, diffs, context=pgcontext)
    if p:
      p.set_text(f"Netlisting {self.project}").incr_max(1).write().incr()
    netlister.resolve()

  def fillvars(self, variables, diffs, pages=None, netlister=None, p=None):
    variables.define(variables.GLOBAL, "CURRENT_DATE", datetime.date.today())
    variables.define(variables.GLOBAL, "PROJECTNAME", self.project)
    for key, value in self.variables.items():
      variables.define(variables.GLOBAL, key, value)
    # Variant variables take precedence over project text variables
    variables.define(variables.GLOBAL, "VARIANT", self.variant)
    variables.define(variables.GLOBAL, "VARIANTNAME", self.variant)
    variables.define(
      variables.GLOBAL, "VARIANT_DESC", self.variants.get(self.variant, "")
    )
    if not pages:
      return
    pgcount = sum(len(i) for i, _ in pages.values())
    variables.define(variables.GLOBAL, variables.PAGECOUNT, pgcount)
    context = self.context()
    if netlister:
      context += netlister.context()
    for filename, (instances, sch) in pages.items():
      if p:
        p.set_text(f"Processing {filename[:-10]}").write().incr()
      for path, sheet in instances:
        pgcontext = context + (path, sheet)
        variables.define(
          pgcontext, variables.PAGENO, int(path.get("page", [0])[0])
        )
        sch.fillvars(variables, diffs, context=pgcontext)
    if p:
      p.set_text(f"Processing {self.project}").incr_max(1).write().incr()

  def get_license(self):
    """Returns the contents of the license file referenced by the LICENSE_FILE
    project variable."""
    for v in self.variables:
      if v.lower() == "license_file":
        license_file = self.variables[v]
        break
    else:
      return ""
    licpath = os.path.join(os.path.dirname(self._fname or ""), license_file)
    if not os.path.isfile(licpath):
      print(f"Unable to load license from {licpath}", file=sys.stderr)
      return ""
    return open(licpath).read()

  def get_license_header(self):
    return self.get_variable("license_header", DEFAULT_LICENSE_HEADER)

  def get_pages(self, projfile, rev, p):
    """Returns a dict mapping filenames to a tuple of ([instances], kicad_sch).
    Instances in turn are a tuple of (path ref, sheet ref)
    """
    pages = {}
    projdir = os.path.dirname(self._fname or "")
    tls = self.json.get("schematic", {}).get(
      "top_level_sheets",
      [
        {
          "filename": f"{self.project}.kicad_sch"  # name, uuid not known
        }
      ],
    )
    # FIXME: flat schematics have the name and uuid defined in the kicad_pro,
    # overriding the uuid in the kicad_sch. All of the root pages share "/"
    # as the path prefix.
    # The whole TOC thing will need to be adjusted to support a list at top
    assert len(tls) == 1, "flat schematics aren't supported yet"
    to_load = [f"{projdir}/" * bool(projdir) + s["filename"] for s in tls]
    if p:
      p.incr_max(len(to_load))
    while to_load:
      filepath = to_load.pop()
      relpath = os.path.relpath(filepath, projdir)
      if p:
        p.set_text(f"Loading {rev + ':' if rev else ''}{relpath}").write()
      f = git.open_rb(filepath, rev)
      try:
        sch = kicad_sch.kicad_sch(f, filepath)
      except kicad_sch.UnsupportedFileError as e:
        msg = f"Unable to load {rev + ':' if rev else ''}{relpath}: {e.reason}"
        if p:
          p.msg(msg)
        else:
          print(msg, file=sys.stderr)
        continue
      # Handle the root page, whose path is self-defined by uuid
      if relpath not in pages:
        assert sch.is_root()
        pages[relpath] = (
          [(kicad_sch.Path.new(""), kicad_sch.Sheet.fake(sch))],
        )
      assert len(pages[relpath]) == 1
      pages[relpath] += (sch,)
      for path, sheet in sch.get_sheets(self.project):
        filepath = sch.relpath(sheet.file(None).v)
        relpath = os.path.relpath(filepath, projdir)
        if relpath not in pages:
          to_load.append(filepath)
          if p:
            p.incr_max()
        pages.setdefault(relpath, ([],))[0].append((path, sheet))
      if p:
        p.incr().write()
    # Prune unreachable instances
    to_remove = []
    for filepath, (instances, _sch) in pages.items():
      for i, (path, sheet) in enumerate(instances):
        uuid = path.uuid(sheet)
        if not self.uuid_to_name(pages, uuid):
          to_remove.append((filepath, i))
    for filepath, i in reversed(to_remove):
      del pages[filepath][0][i : i + 1]
      if not pages[filepath][0]:
        del pages[filepath]
    self._sheet_insts = {
      path.uuid(sheet): (path, sheet)
      for instances, _sch in pages.values()
      for path, sheet in instances
    }
    self._sheet_flag_cache = {}
    return pages

  def get_worksheet(self, rev, p):
    """Returns a kicad_wks instance."""
    default_wks = kicad_wks.kicad_wks(None)
    wks_path = self.json.get("schematic", {}).get("page_layout_descr_file")
    if not wks_path:
      return default_wks
    if p:
      p.incr_max().set_text(f"Loading {wks_path}").write().incr()
    os.environ.update(config_env_vars())
    wks_path_expanded = os.path.expandvars(wks_path)
    if any(c in wks_path_expanded for c in "$%~"):
      if p:
        p.clear()
      print(
        "WARNING: unable to expand worksheet path", wks_path, file=sys.stderr
      )
      return default_wks
    if wks_path_expanded.startswith("/"):
      if not os.path.isfile(wks_path_expanded):
        if p:
          p.clear()
        print(
          "WARNING: unable to find worksheet",
          wks_path_expanded,
          file=sys.stderr,
        )
        return default_wks
      wks = kicad_wks.kicad_wks(open(wks_path_expanded), wks_path_expanded)
    else:
      projdir = os.path.dirname(self._fname or "")
      if projdir:
        wks_path_expanded = f"{projdir}/{wks_path_expanded}"
      wks = kicad_wks.kicad_wks(
        git.open_rb(wks_path_expanded, rev), wks_path_expanded
      )
    return wks or default_wks

  def gen_toc(self, pages):
    # Returns a sorted, hierarchical TOC, lists of dicts containing lists.
    # Each entry is a dict containing page#, name, uuid, filepath, sch, children
    # hier: an intermediate mapping of {uuidpart: instdict}.
    #       instdict contains a "hier" attribute of the same
    hier = {}
    for filepath, (instances, sch) in pages.items():
      for path, sheet in instances:
        uuid = path.uuid(sheet)
        pageno = path.get("page", [0])[0]
        if isinstance(pageno, str):
          pageno = int(pageno.strip("#") or 0)
        inst = {
          "page": pageno,
          "name": self.uuid_to_name(pages, uuid),
          "uuid": uuid,
          "file": filepath,
          "sch": sch,
        }
        assert inst["name"]  # stale instances should have been pruned already
        subhier = hier
        uuidparts = uuid.split("/")
        for subid in uuidparts[1:-1]:
          subhier = subhier.setdefault(subid, {}).setdefault("hier", {})
        subhier.setdefault(uuidparts[-1], {}).update(inst)

    # Collapse into lists-of-lists, sorted by PN
    def to_list(hier):
      return [
        {
          "children" if k == "hier" else k: to_list(v) if k == "hier" else v
          for k, v in inst.items()
        }
        for inst in sorted(hier.values(), key=lambda i: (i["page"], i["name"]))
      ]

    return to_list(hier)

  def uuid_to_name(self, pages, uuid):
    # Returns the sheet instance name based on uuid, project and its pages
    uuid = uuid.split("/")[2:]
    if not uuid:
      return "/"
    name = [""]
    file = f"{self.project}.kicad_sch"
    for sheetuuid in uuid:
      sch = pages[file][-1]
      for sheet in sch["sheet"]:
        if sheet.uuid() == sheetuuid:
          name.append(sheet.name(None).v)
          file = os.path.dirname(file)
          file = f"{file}/{sheet.file(None).v}" if file else sheet.file(None).v
          break
      else:
        return None
    return "/".join(name)

  def __eq__(self, other):
    raise NotImplementedError()

  def apply(self, key, data):
    raise NotImplementedError()


def config_env_vars():
  """Searches kicad configuration directories for environment variable defines
  and returns a dictionary of all the assignments.
  """
  configdirs = []
  for basedir in (
    "$HOME/.config/kicad",  # Linux
    "%AppData%/kicad",  # Windows
    "$HOME/Library/Preferences/kicad",  # macOS
  ):
    basedir = os.path.expandvars(basedir)
    if not os.path.isdir(basedir):
      continue
    for subdir in os.listdir(basedir):
      if (
        subdir.partition(".")[0].isdecimal()
        and subdir.partition(".")[2].isdecimal()
      ):
        configdirs.append(os.path.join(basedir, subdir))

  # Parse files oldest to newest
  def sortkey(p):
    base = os.path.basename(p).partition(".")
    return (int(base[0]), int(base[2]), p)

  configdirs = sorted(configdirs, key=sortkey)
  # varibales in KICAD_CONFIG_HOME override all
  if "KICAD_CONFIG_HOME" in os.environ:
    configdirs.append(os.environ["KICAD_CONFIG_PATH"])
  envvars = {}
  for configdir in configdirs:
    for configfile in os.listdir(configdir):
      if configfile.lower() == "kicad_common.json":
        config = json.load(open(os.path.join(configdir, configfile)))
        envvars.update(
          ((config.get("environment") or {}).get("vars") or {}).items()
        )
        break
  return envvars


def kicad_pro(f, fname=None):
  data_filter_func = getattr(kicad_pro, "data_filter_func", None)
  return KicadPro(f, fname, data_filter_func=data_filter_func)


def main(argv):
  """USAGE: kicad_pro.py [kicad_pro]
  Reads a kicad_pro from stdin or symfile and writes out the page tree.
  """
  path = argv[1] if len(argv) > 1 else None
  p = progress.Progress(sys.stderr)
  p.set_max(1).set_text(f"Loading {path or 'stdin'}").write()
  with open(path) if path else sys.stdin as f:
    proj = kicad_pro(f, path)
  p.incr()
  pages = proj.get_pages(None, None, p=p)
  toc = proj.gen_toc(pages)
  p.clear()
  print(f"{path or 'stdin'}:")

  def print_toc(toc, indent=0):
    for inst in toc:
      print(
        f"{inst['page']:3d}: {'  ' * indent}{inst['name']} ({inst['file']})"
      )
      print_toc(inst.get("children", []), indent + 1)

  print_toc(toc)


if __name__ == "__main__":
  sys.exit(main(sys.argv))
