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

import argparse
import os
import re
import sys
import time

from . import git, kicad_pro, kicad_sch, kicad_wks
from .diff import threeway
from .diffui import DiffUI
from .netlister import Netlister
from .progress import Progress


class GenSchError(Exception):
  pass


class Page:
  """Manages a single page."""

  PAGENAME_CREATED = "[CREATED]"
  PAGENAME_DELETED = "[DELETED]"

  def __init__(self, names, pages):
    self.names = names
    self.schs = [p[1] for p in pages]
    self.insts = [p[0] for p in pages]

  def sch(self):
    return self.schs[0]

  def instances(self):
    return self.insts[0]

  def dispname(self):
    names = self.names
    if names[0] == Page.PAGENAME_CREATED:
      names = [name if name != Page.PAGENAME_DELETED else "-" for name in names]
    if not any(name != names[0] for name in names[1:]):
      names = names[:1]
    names = [self._pretty_name(name) for name in names]
    name = names[0]
    if len(names) > 1:
      name += " > " + " / ".join(names[1:])
    return name

  def _pretty_name(self, name):
    if "." in os.path.basename(name):
      name = name.rpartition(".")[0]
    if "schematicFile" in name:
      name = name.rpartition("schematicFile")
      name = name[0] + name[2][1:]
    return name

  def diff(self):
    diffs = []
    conflicts = []
    if len(self.schs) == 2:
      for difftree in self.schs[0].diff(self.schs[1]):
        diffs += [([d], []) for d in difftree._flatten()]
    elif len(self.schs) == 3:
      conflicts = threeway(
        self.schs[0], self.schs[1], self.schs[2], return_safe=diffs
      )
    return (diffs, conflicts)


class Schematic:
  """Manages a single project with conflicts."""

  def __init__(self, proj):
    """Creates an empty project with no files."""
    self.diff = False
    self._license = None
    self._license_header = None
    self._worksheet = None
    self._proj = proj
    self._revs = []

  def add_rev(self, rev):
    if rev not in self._revs:
      self._revs.append(rev)

  def _genui(self, v=0):
    p = Progress(sys.stderr if v >= 0 else None)

    if not self._revs:
      self._revs.append("")

    projs = []
    worksheets = []
    pagesets = []
    for rev in self._revs:
      p.set_text("Loading " + self._proj)
      p.set_incr_max_mult(4).set_val(0).set_max(1).write().incr()
      f = git.open_rb(self._proj, rev)
      projs.append(kicad_pro.kicad_pro(f, fname=self._proj))
      pagesets.append(projs[-1].get_pages(self._proj, rev, p))
      worksheets.append(projs[-1].get_worksheet(rev, p))

    if p:
      p.set_text("Processing hierarchy").set_incr_max_mult().incr_max(2).write()
    ## FIXME: how to handle the case where multiple people added pages?
    # for i in range(1, len(pagesets)):
    #  pagesets[i] = matchlists(pagesets[0], pagesets[i])
    # page_annotation = Page.PAGENAME_CREATED
    # for pageset in pagesets:
    #  pageset += [None for i in range(max(map(len, pagesets)) - len(pageset))]
    #  pageset[:] = [page or sch.sch(None, page_annotation)
    #                for page in pageset]
    #  page_annotation = Page.PAGENAME_DELETED
    # pages = list(map(Page, zip(*pagesets)))
    # 1. union of all pages keys
    # 2. zip the pages values for the keys
    # 3. handle the ([(uuid, path), ...], sch) in Page
    # 4. add added/deleted flags to Page?
    pages = [Page([n], [p]) for n, p in pagesets[0].items()]
    if p:
      p.incr().write()

    # Start with the TOC for the first project, FIXME: diffs
    toc = projs[0].gen_toc(pagesets[0])
    if p:
      p.incr().write()

    # Fill in netlist and variables
    # FIXME: handle diffs correctly
    netlister = Netlister()
    variables = kicad_sch.Variables()
    for pageset in pagesets:
      projs[0].fillnetlist(netlister, [], pageset, p=p)
      projs[0].fillvars(variables, [], pageset, netlister=netlister, p=p)

    ver = git.get_version(os.path.dirname(self._proj))
    ver += time.strftime(" (%Y-%m-%d %H:%M)")
    ver = " vs ".join(r or ver for r in self._revs)
    title = self._proj.lstrip("./\\")
    if self.diff:
      title += " (only diffs)"
    p.set_text("Rendering " + self._proj).write()
    ui = DiffUI(
      title=title,
      ver=ver,
      proj=projs[0],
      worksheet=self._worksheet or worksheets[0],
      variables=variables,
      netlister=netlister,
      license_text=self._license,
      license_header=self._license_header,
      mode=len(self._revs),
    )
    for page in pages:
      # FIXME: handle changing paths
      dispname = page.dispname()
      p.set_text("Rendering " + dispname).incr().write()
      diffs, conflicts = page.diff()
      if self.diff and not conflicts:
        # Skip the page if only unimportant diffs remain
        for pair in diffs or []:
          if (
            pair[0]
            and not all(d.is_unimportant() for d in pair[0])
            or pair[1]
            and not all(d.is_unimportant() for d in pair[1])
          ):
            break
        else:
          continue
      ui.addpage(dispname, page.sch(), page.instances(), diffs, conflicts)
    ui.set_toc(toc)

    p.clear()
    return ui

  def write(self, path, v=0):
    ui = self._genui(v=v)
    if not path or path == "-":
      path = ""
    elif path[-1] in ("/", "\\") or os.path.isdir(path):
      path = os.path.join(path, re.sub(r"[^\w.-]", "_", ui.title)) + ".html"
    if v >= 0:
      sys.stderr.write("Writing to %s\n" % (path or "stdout"))
    with sys.stdout.buffer if not path else open(path, "wb") as f:
      f.write(("\n".join(ui.genhtml())).encode("UTF-8"))

  def launch_ui(self, v=0):
    ui = self._genui(v=v)
    ui.getresponse()


def main(argv):
  parser = argparse.ArgumentParser(
    prog="kischvidimer schgen",
    description="""Generates and displays a schematic""",
  )
  parser.add_argument(
    "-q",
    "--quiet",
    action="count",
    default=0,
    help="make the console output quieter",
  )
  parser.add_argument(
    "-v",
    "--verbose",
    action="count",
    default=0,
    help="make the console output more verbose",
  )
  parser.add_argument(
    "-o",
    "--output",
    metavar="HTML|DIR",
    help="write to a file/directory instead of displaying the UI",
  )
  parser.add_argument(
    "-l",
    "--license",
    "--license-file",
    help="add LICENSE file to UI, overriding the LICENSE_FILE project variable",
  )
  parser.add_argument(
    "--license-header",
    help="add custom license header text to the file; overrides LICENSE_HEADER",
  )
  parser.add_argument(
    "-s",
    "--scrub",
    metavar="REGEX",
    action="append",
    help="remove any matching schematic data prior to processing the file",
  )
  parser.add_argument(
    "-w",
    "--worksheet",
    help="override the page border specified in the project",
  )
  # parser.add_argument(
  #  "-d", "--diff", action="store_true", help="only render pages with changes"
  # )
  # parser.add_argument(
  #  "-c", "--conflicts-ok", action="store_true", help="ignore conflict markers"
  # )
  parser.add_argument(
    "git_rev",
    nargs="?",  # nargs="*",
    help="git revision to render",
    # ", rev range for a diff, or 3 revisions for a merge"
  )
  parser.add_argument(
    "project",
    help=".kicad_pro to render",
  )
  args = parser.parse_args(argv[1:])
  verbosity = args.verbose - args.quiet

  sch = Schematic(proj=args.project)

  # Overrides
  if args.license is not None:
    sch._license = open(args.license).read() if args.license else ""
  if args.license_header:
    sch._license_header = args.license_header
  if args.scrub is not None:
    s_re = re.compile("|".join(args.scrub))
    kicad_pro.kicad_pro.data_filter_func = lambda d: s_re.sub("", d)
    kicad_sch.kicad_sch.data_filter_func = lambda d: s_re.sub("", d)
  if args.worksheet is not None:
    sch._worksheet = kicad_wks.kicad_wks(
      open(args.worksheet) if args.worksheet else None
    )

  # Diff flags
  # sch.diff = args.diff
  # FIXME: conflict marker handling
  # if args.conflicts_ok:
  #  conflicts_ok = (kicad_sch.kicad_sch.FileError,)
  # else:
  conflicts_ok = ()

  # Git revisions
  # TODO: handle lists of revs for diffs
  for rev in [args.git_rev] if args.git_rev else []:
    revlist = git.rev_parse(rev, repo=os.path.dirname(args.project))
    if revlist:
      revlist.reverse()
      if rev.endswith(".."):
        revlist[1] = ""
      for rev in revlist:
        sch.add_rev(rev)
    else:
      print(f"Skipping '{rev}'", file=sys.stderr)

  # Execute
  try:
    if args.output is None:
      sch.launch_ui(v=verbosity)
    else:
      sch.write(args.output, v=verbosity)
  except conflicts_ok as e:
    if not e.is_conflict:
      raise
    print(e, file=sys.stderr)

  return 0


if __name__ == "__main__":
  try:
    sys.exit(main(sys.argv))
  except GenSchError as e:
    print(f"Error: {e}", file=sys.stderr)
    sys.exit(1)
