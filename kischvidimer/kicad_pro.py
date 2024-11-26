#!/usr/bin/env python3
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

from .diff import Comparable, Diff, Param, difflists, applylists, targetdict
from . import git
from . import kicad_sch
from . import kicad_wks
from . import progress

class kicad_pro(Comparable):
  """ Kicad project file """
  def __init__(self, f, fname=None):
    self._fname = fname
    self.json = json.loads(f.read())

  @property
  def project(self):
    return self.json["meta"]["filename"].replace(".kicad_pro", "")

  @property
  def pgcount(self):
    return len(self.json.get("sheets", []))

  @property
  def variables(self):
    return self.json.get("text_variables", {})

  def context(self):
    s = kicad_sch.sexp.sexp.init([kicad_sch.sexp.atom("~project"), self.project])
    return (s,)

  def fillvars(self, variables, diffs, pages=None, p=None):
    variables.define(variables.GLOBAL, "CURRENT_DATE", datetime.date.today())
    variables.define(variables.GLOBAL, "PROJECTNAME", self.project)
    for key, value in self.variables.items():
      variables.define(variables.GLOBAL, key, value)
    if not pages:
      return
    pgcount = kicad_pro.pgcount(pages)
    variables.define(variables.GLOBAL, variables.PAGECOUNT, pgcount)
    context = self.context()
    for filename, (instances, sch) in pages.items():
      if p: p.write().incr()
      for path, sheet in instances:
        pgcontext = context + (path, sheet)
        variables.define(pgcontext, variables.PAGENO, int(path.get("page", [0])[0]))
        sch.fillvars(variables, diffs, context=pgcontext)

  def get_pages(self, projfile, rev, p):
    """ Returns a dict mapping filenames to a tuple of ([instances], kicad_sch).
    Instances in turn are a tuple of (path ref, sheet ref)
    """
    pages = {}
    projdir = os.path.dirname(self._fname or "")
    to_load = [f"{projdir}/{self.project}.kicad_sch" if projdir
               else f"{self.project}.kicad_sch"]
    if p: p.incr_max()
    while to_load:
      filepath = to_load.pop()
      relpath = os.path.relpath(filepath, projdir)
      if p: p.set_text(f"Loading {rev+':' if rev else ''}{relpath}").write()
      f = git.open_rb(filepath, rev)
      sch = kicad_sch.kicad_sch(f, filepath)
      # Handle the root page, whose path is self-defined by uuid
      if relpath not in pages:
        root_path = sch.root_path
        assert root_path is not None
        pages[relpath] = ([(kicad_sch.fakepath(""), kicad_sch.fakesheet(sch))],)
      assert len(pages[relpath]) == 1
      pages[relpath] += (sch,)
      for path, sheet in sch.get_sheets(self.project):
        filepath = sch.relpath(sheet.file)
        relpath = os.path.relpath(filepath, projdir)
        if relpath not in pages:
          to_load.append(filepath)
          if p: p.incr_max()
        pages.setdefault(relpath, ([],))[0].append((path, sheet))
      if p: p.incr().write()
    return pages

  def get_worksheet(self, rev, p):
    """ Returns a kicad_wks instance. """
    default_wks = kicad_wks.kicad_wks(None)
    wks_path = self.json.get("schematic", {}).get("page_layout_descr_file")
    if not wks_path:
      return default_wks
    if p: p.incr_max().set_text(f"Loading {wks_path}").write().incr()
    os.environ.update(config_env_vars())
    wks_path_expanded = os.path.expandvars(wks_path)
    if any(c in wks_path_expanded for c in "$%~"):
      if p: p.clear()
      print("WARNING: unable to expand worksheet path", wks_path,
            file=sys.stderr)
      return default_wks
    if wks_path_expanded.startswith("/"):
      if not os.path.isfile(wks_path_expanded):
        if p: p.clear()
        print("WARNING: unable to find worksheet", wks_path_expanded,
              file=sys.stderr)
        return default_wks
      wks = kicad_wks.kicad_wks(open(wks_path_expanded, "r"), wks_path_expanded)
    else:
      wks = kicad_wks.kicad_wks(git.open_rb(wks_path_expanded, rev), wks_path_expanded)
    return wks or default_wks

  @staticmethod
  def pgcount(pages):
    return sum(len(i) for i,_ in pages.values())

  def gen_toc(self, pages):
    # Returns a sorted, hierarchical TOC, lists of dicts containing lists.
    # Each entry is a dict containing page#, name, uuid, filepath, sch, children
    # hier: an intermediate mapping of {uuidpart: instdict}. instdict contains a "hier"
    #       attribute of the same
    hier = {}
    for filepath, (instances, sch) in pages.items():
      for path, sheet in instances:
        uuid = path.uuid(sheet)
        inst = {
            "page": int(path.get("page", [0])[0]),
            "name": self.uuid_to_name(pages, uuid),
            "uuid": uuid,
            "file": filepath,
            "sch":  sch,
            }
        subhier = hier
        uuidparts = uuid.split("/")
        for subid in uuidparts[1:-1]:
          subhier = subhier.setdefault(subid, {}).setdefault("hier", {})
        subhier.setdefault(uuidparts[-1], {}).update(inst)
    # Collapse into lists-of-lists, sorted by PN
    def to_list(hier):
      return [
          {"children" if k == "hier" else k: to_list(v) if k == "hier" else v
            for k,v in inst.items()}
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
          name.append(sheet.name)
          file = os.path.dirname(file)
          file = f"{file}/{sheet.file}" if file else sheet.file
          break
      else:
        return None
    return "/".join(name)


def config_env_vars():
  """ Searches kicad configuration directories for environment variable defines
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
      if (subdir.partition(".")[0].isdecimal() and
          subdir.partition(".")[2].isdecimal()):
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
        config = json.load(open(os.path.join(configdir, configfile), "r"))
        envvars.update(config.get("environment", {}).get("vars", {}).items())
        break
  return envvars


def main(argv):
  """USAGE: kicad_pro.py [kicad_pro]
  Reads a kicad_pro from stdin or symfile and writes out the page tree.
  """
  p = progress.Progress(sys.stderr)
  p.set_max(1).set_text(f"Loading {argv[0] if argv else 'stdin'}").write()
  with open(argv[0], "r") if argv else sys.stdin as f:
    proj = kicad_pro(f, argv[0] if argv else None)
  p.incr()
  pages = proj.get_pages(None, None, p=p)
  toc = proj.gen_toc(pages)
  p.clear()
  print(f"{argv[0] if argv else 'stdin'}:")
  def print_toc(toc, indent=0):
    for inst in toc:
      print(f"{inst['page']:3d}: {'  '*indent}{inst['name']} ({inst['file']})")
      print_toc(inst.get("children", []), indent+1)
  print_toc(toc)

if __name__ == '__main__':
  sys.exit(main(sys.argv[1:]))
