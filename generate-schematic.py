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
import os
import re
import sys
import time
from diff import matchlists, threeway
from diffui import DiffUI
from progress import Progress
import filetypes
import git

class GenSchError(Exception):
  pass

class Page(object):
  """Manages a single page."""
  PAGENAME_CREATED = '[CREATED]'
  PAGENAME_DELETED = '[DELETED]'

  def __init__(self, pages):
    self.pgFiles = pages
  def pgFile(self):
    return self.pgFiles[0]
  def dispname(self):
    names = [pgFile._name for pgFile in self.pgFiles]
    if names[0] == Page.PAGENAME_CREATED:
      names = [name if name != Page.PAGENAME_DELETED else '-' for name in names]
    if not any((name != names[0] for name in names[1:])):
      names = names[:1]
    names = [self._pretty_name(name) for name in names]
    name = names[0]
    if len(names) > 1:
      name += ' > %s' % ' / '.join(names[1:])
    return name
  def _pretty_name(self, name):
    if '.' in os.path.basename(name):
      name = name.rpartition('.')[0]
    datadir = self._datadir()
    if datadir and name.startswith(datadir):
      name = name[len(datadir)+1:]
    if 'schematicFile' in name:
      name = name.rpartition('schematicFile')
      name = name[0] + name[2][1:]
    return name
  def _datadir(self):
    for pgFile in self.pgFiles:
      if pgFile._name.endswith('].pgFile'):
        continue
      datadir = pgFile._name
      while datadir not in ('', '/', 'C:/') and os.path.basename(datadir) != 'datadir':
        datadir = os.path.dirname(datadir)
      return datadir
    return ''
  def diff(self):
    diffs = []
    conflicts = []
    if len(self.pgFiles) == 2:
      for difftree in self.pgFiles[0].diff(self.pgFiles[1]):
        diffs += [([d], []) for d in difftree._flatten()]
    elif len(self.pgFiles) == 3:
      conflicts = threeway(self.pgFiles[0], self.pgFiles[1], self.pgFiles[2],
                           return_safe=diffs)
    return (diffs, conflicts)

class Schematic(object):
  """Manages a single project with conflicts."""

  def __init__(self, proj):
    """Creates an empty project with no files.
    """
    self.diff = False
    self._proj = proj
    self._revs = []

  def add_rev(self, rev):
    if rev not in self._revs:
      self._revs.append(rev)

  def _genui(self, v=0):
    p = Progress(sys.stderr if v >= 0 else None)
    if not self._revs:
      self._revs.append('')

    projs = []
    pagesets = []
    for rev in self._revs:
      p.set_text('Loading %s' % self._proj).set_val(0).set_max(1).write().incr()
      f = git.open_rb(self._proj, rev)
      projs.append(filetypes.proj.proj(f, fname=self._proj))
      projs[-1].load_site()
      projs[-1].libraries(self._proj, rev)
      pagesets.append(projs[-1].get_pages(self._proj, rev, p))

    # FIXME: how to handle the case where multiple people added pages?
    for i in range(1, len(pagesets)):
      pagesets[i] = matchlists(pagesets[0], pagesets[i])
    page_annotation = Page.PAGENAME_CREATED
    for pageset in pagesets:
      pageset += [None for i in range(max(map(len, pagesets)) - len(pageset))]
      pageset[:] = [page or filetypes.pgFile.pgFile(None, page_annotation)
                    for page in pageset]
      page_annotation = Page.PAGENAME_DELETED
    pages = list(map(Page, zip(*pagesets)))

    now = time.strftime('%Y-%m-%d.%H:%M')
    title = '%s - %s' % (self._proj.lstrip('./\\'),
                         ' vs '.join(r or now for r in self._revs))
    if self.diff:
      title += ' (only diffs)'
    p.set_text('Rendering %s' % self._proj)
    p.set_val(0).set_max(len(pages)+1).write()
    # FIXME: TOCs change with diffs...
    projs[0].append_toc([page.pgFile() for page in pages])
    ui = DiffUI(title=title, proj=projs[0], mode=len(self._revs))
    for page in pages:
      # FIXME: handle changing paths
      dispname = page.dispname()
      p.set_text('Rendering %s' % dispname).incr().write()
      diffs, conflicts = page.diff()
      if self.diff and not conflicts:
        # Skip the page if only unimportant diffs remain
        for pair in diffs or []:
          if (pair[0] and not all(d.is_unimportant() for d in pair[0]) or
              pair[1] and not all(d.is_unimportant() for d in pair[1])):
            break
        else:
          continue
      ui.addpage(dispname, page.pgFile(), diffs, conflicts)

    p.clear()
    return ui

  def write(self, path, v=0):
    ui = self._genui(v=v)
    if not path or path == '-':
      path = ''
    elif path[-1] in ('/', '\\') or os.path.isdir(path):
      path = os.path.join(path, re.sub('[^\w.-]', '_', ui.title)) + '.html'
    if v >= 0:
      sys.stderr.write('Writing to %s\n' % (path or 'stdout'))
    with (sys.stdout.buffer if not path else open(path, 'wb')) as f:
      f.write(('\n'.join(ui.genhtml())).encode('UTF-8'))

  def launch_ui(self, v=0):
    ui = self._genui(v=v)
    ui.getresponse()


def main(argv):
  if len(argv) == 1:
    sys.stderr.write(
"""%s [-o HTML|DIR|-] [--diff] [GIT_REV [GIT_REV]] project.proj
Generates and displays a schematic.
If GIT_REV is provided, generates a schematic diff between two or three
revisions. Trailing ..'s in GIT_REV will compare to the working tree.
  --diff  Hides any pages that do not have differences.
""" % argv[0])
    return 2
  sch = Schematic(proj=None)
  output = None
  conflicts_ok = tuple()
  verbosity = 0
  args = iter(argv[1:])
  for f in args:
    if f in ('--diff', '-d'):
      sch.diff = True
    elif f in ('--output', '-o'):
      output = f.partition('=')[2] if '=' in f else next(args)
    elif f == '--conflicts-ok':
      conflicts_ok = (filetypes.pgFile.pgFile.FileError,)
    elif f in ('--quiet', '--verbose', '-q', '-v'):
      verbosity += 1 if 'v' in f else -1
    elif f.endswith('.proj'):
      if sch._proj:
        sys.stderr.write('Ignoring extra proj %s\n' % f)
      else:
        sch._proj = f
    else:
      revlist = git.rev_parse(f)
      if revlist:
        revlist.reverse()
        if f.endswith('..'):
          revlist[1] = ''
        for rev in revlist:
          sch.add_rev(rev)
      else:
        sys.stderr.write("Skipping '%s'\n" % f)
  if sch._proj is None:
      raise GenSchError('Input .proj file is required')
  try:
    if output is None:
      sch.launch_ui(v=verbosity)
    else:
      sch.write(output, v=verbosity)
  except conflicts_ok as e:
    if not e.is_conflict:
      raise
    sys.stderr.write('%s\n' % e)
  return 0
if __name__ == '__main__':
  try:
    sys.exit(main(sys.argv))
  except GenSchError as e:
    sys.stderr.write('Error: %s\n' % e)
    sys.exit(1)