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
import subprocess
import sys
import time
import diff
from diffui import DiffUI
import filetypes
import git

class Project(object):
  """Manages a single project with conflicts."""

  # Modes of merge/conflict resolution; OR'd together
  MODE_SAFE     = 0     # Only does simple --ours or --theirs merges
  MODE_REWRITE  = 1<<0  # Allows total rewrites of files that have parsers
  MODE_UI       = 1<<1  # Allows merges that require manual conflict resolution
  MODE_FORCE_UI = 3<<1  # Forces manual resolution of non-conflicting merges
  MODE_ALL      = MODE_REWRITE | MODE_UI  # All possible merges
  # Various resolution states of a file
  STATE_OUT_OF_DATE = -1  # File can be regenerated or arbitrarily picked
  STATE_OK          =  0  # Conflict has been resolved in git
  STATE_OURS        =  1  # Conflict can be resolved by choosing --ours
  STATE_THEIRS      =  2  # Conflict can be resolved by choosing --theirs
  STATE_BOTH        =  3  # Conflict can be merged by automatic means
  STATE_USER        =  4  # Conflict can be merged by user intervention
  STATE_CONFLICT    =  5  # Conflict cannot be resolved by automatic means
  STATE_UNKNOWN     =  6  # State has not been determined yet
  STATES_NEED_MERGE = (STATE_BOTH, STATE_USER)
  STATES_UNRESOLVABLE = (STATE_CONFLICT, STATE_UNKNOWN)
  # Converts a resolution state to simple text + verbosity threshold
  _RESOLUTION_DESCRIPTION = {
      STATE_OUT_OF_DATE: ('should be regenerated', 2),
      STATE_OK: ('nobody actually changed', 3),
      STATE_OURS: ('only you modified', 1),
      STATE_THEIRS: ('others modified', 1),
      STATE_BOTH: {  # Selected by mode & MODE_REWRITE
        MODE_SAFE: (
          'both you and others modified (requires --rewrite to fix)', 0),
        MODE_REWRITE: (
          'both you and others modified in an automatically-resolvable way', 1)
      },
      STATE_USER: {  # Selected by mode & (MODE_REWRITE|MODE_UI)
        MODE_SAFE: (
          'both you and others modified (requires --rewrite --ui to fix)', 0),
        MODE_REWRITE: (
          'both you and others modified (requires --ui to fix)', 0),
        MODE_UI: (
          'both you and others modified (requires --rewrite --ui to fix)', 0),
        MODE_REWRITE|MODE_UI: (
          'both you and others modified in a manually-resolvable way', 1)
      },
      STATE_CONFLICT: ('both you and others modified in a conflicting way', 0),
      STATE_UNKNOWN: ('may or may not have been modified', 0),
  }
  # Map used to flip ours/theirs when in a rebase
  _RESOLUTION_REBASE_MAP = {
      STATE_OURS: STATE_THEIRS,
      STATE_THEIRS: STATE_OURS,
  }
  # Hierwrite regexes
  RE_HIERWRITE_PAGE = re.compile(
      'INFO[(]SPCOCN-1028[)]: writing ' +
      '<(?P<proj>[^>]*)>(?P<block>[^.]*).SCH.(?P<sch>\d*).(?P<page>\d*):?$')

  def __init__(self, project):
    """Creates an empty project with no files.
    project -- git-absolute path to the project directory.
               This directory should directly contain the proj file only.
    """
    self._timestamp = time.strftime('%c')
    self._pages = {}
    self._databases = {}
    self._projs = {}
    self._netlisted = set()
    self._project = project
    self._conflicts = {}

  def add_page(self, page, extension, state):
    """Adds an extension for a page; initially of unknown state.
    page      -- git-absolute path to the page.
    extension -- extension of the page file (pgFile, etc)
    state     -- conflict state of the file (provided by git)
    """
    # FIXME: handle inserts/moves/deletes by looking at the .pgFile PAGE_NUMBER,
    # which doesn't change in move operations
    self._pages.setdefault(page, {}).setdefault(
        extension, [Project.STATE_UNKNOWN, state])

  def add_proj(self, proj, state):
    """Adds a project proj file; initially of unknown state.
    proj   -- git-absolute path to the proj file.
    state -- conflict state of the file (provided by git)
    """
    self._projs.setdefault(proj, [Project.STATE_UNKNOWN, state])

  def add_database(self, database, state):
    """Adds a database file; initially of unknown state.
    database -- git-absolute path to the database file.
    state    -- conflict state of the file (provided by git)
    """
    self._databases.setdefault(database, [Project.STATE_UNKNOWN, state])

  def __str__(self):
    """Generates a human-readable state description of the project."""
    return self.summary()

  def summary(self, mode=MODE_SAFE, include_resolved=True, verbosity=0):
    """Generates a human-readable state description of the project.
    mode -- summarized based on the allowed merge mode
    include_resolved -- if false, will filter out STATE_OK files
    verbosity -- the higher the number the more verbose the output is
    """
    s  = ''
    for d, n in (
          (self._projs, 'project file'),
          (self._databases, 'database file'),
          (self._pages, 'page'),
        ):
      s += self._str_summary(d, n, mode, include_resolved, verbosity)
    if self._conflicts and verbosity >= 1:
      s += '%s\n' % diff.conflicts_to_str(self._conflicts)
    return "Project '%s' has %s\n%s" % (self._project.strip('/\\'),
        'the following conflicts:' if len(s) > 1 else 'no conflicts', s)

  def _str_summary(self, d, name, mode, include_resolved, verbosity):
    """Generates a human-readable state description for one part of the project.
    d    -- dictionary of some sort (one of _pages, _database, _projs, etc)
    name -- human-readable text describing the part of the project
    mode -- summarized based on the allowed merge mode
    include_resolved -- if false, will filter out STATE_OK files
    verbosity -- the higher the number the more verbose the output is
    """
    desc = Project._RESOLUTION_DESCRIPTION
    if git.is_rebase():
      desc = { Project._RESOLUTION_REBASE_MAP.get(k, k): v
               for k,v in desc.items() }
    state_summary = {k: self._state_summary(v) for k,v in d.items()}
    state_values = list(state_summary.values())
    state_counts = {x: state_values.count(x) for x in set(state_values)}
    s = ''
    for state, count in state_counts.items():
      if not include_resolved and state == Project.STATE_OK:
        continue
      description = desc[state]
      if state == Project.STATE_BOTH:
        description = description[mode & Project.MODE_REWRITE]
      elif state == Project.STATE_USER:
        description = description[mode & (Project.MODE_REWRITE|Project.MODE_UI)]
      s += '  %3d %s%s that %s' % (count, name, 's'*(count!=1), description[0])
      if description[1] <= verbosity:
        s += ':'
        for f in state_summary:
          if state_summary[f] == state:
            s += '\n      | %s' % f
      s += '\n'
    return s

  def _find_extensions(self, extension, subdir=''):
    """Returns git-absolute paths in the project tree with the chosen extension.
    extension -- extension to search for
    Note that files may not exist in the working copy if they have been deleted.
    """
    extension = '.%s' % extension.lstrip('.')
    if subdir:
      subdir = '%s/' % subdir.rstrip('/\\')
    return [f for f in git.ls_tree(self._project, 'HEAD')
            if f.endswith(extension) and f.startswith(subdir)]

  def _add_projs(self):
    """Searches for projs in the project tree and adds ones not already tracked
    as non-conflicting. This should be called after all other files have been
    added so that other databases can be regenerated even if the projs themselves
    are unmodified or unconflicting.
    """
    for proj in self._find_extensions('.proj'):
      self._projs.setdefault(proj, [Project.STATE_OK, git.STATE_MODIFIED*2])

  def _state_summary(self, it, mode=MODE_ALL):
    """Returns a summary of the state across a bunch of items.
    it -- iterable, dict, or individual state to summarize
    mode -- whether to interpret "both" and/or "ui" as "conflict"
            For instance, if rewriting is allowed, "both" is
            resolvable. If rewriting is not allowed, "both" is an
            irresolvable conflict if it's for a single file, but for
            groups of files it's fine.
    """
    both_is_conflict = not (mode & Project.MODE_REWRITE)
    user_is_conflict = not (mode & Project.MODE_UI)
    if isinstance(it, int):
      it = [(it, None)]
    elif isinstance(it, list):
      it = [it]
    elif isinstance(it, set):
      it = [(x, None) for x in it]
    elif isinstance(it, dict):
      it = it.values()
    state = Project.STATE_OK
    for filestate, _ in it:
      if both_is_conflict and filestate == Project.STATE_BOTH:
        return Project.STATE_CONFLICT
      if user_is_conflict and filestate == Project.STATE_USER:
        return Project.STATE_CONFLICT
      if filestate in Project.STATES_UNRESOLVABLE:
        return filestate
      if filestate == Project.STATE_USER:
        state = filestate
      elif (filestate == state or filestate == Project.STATE_OK
            or state in Project.STATES_NEED_MERGE):
        pass
      elif state in (Project.STATE_OK, Project.STATE_OUT_OF_DATE):
        state = filestate
      elif filestate == Project.STATE_OUT_OF_DATE:
        pass
      elif Project.STATE_OURS <= filestate <= Project.STATE_BOTH:
        state = Project.STATE_BOTH
      else:
        raise Exception('Unhandled case %d vs %d\n' % (state, filestate))
    return state

  def file_count(self):
    """Returns the number of files in this project."""
    return (len(self._projs) + len(self._databases)
        + sum(map(len, self._pages.values())))

  def state(self, mode=MODE_SAFE):
    """Returns a single STATE_* summarizing the entire project."""
    return self._state_summary({
      self._state_summary(self._projs, mode),
      self._state_summary(self._databases, mode),
      self._state_summary({
        self._state_summary(self._pages[page], mode)
        for page in self._pages
      }, mode=Project.MODE_ALL),
    })

  def apply(self, mode=MODE_SAFE, progress=None):
    """Applies all the automatic resolutions possible.
    mode -- Controls what kinds of merges are attempted.
            For example, allowing rewrites attempts to rewrite files based on
            reverse-engineering.  This increases the kinds of things we can
            merge, but may break with obscure features.
    """
    for proj in self._projs:
      if progress:
        progress.set_text('Resolving %s' % proj).write().incr()
      # TODO: support merging of projs
      if self._git_pick(proj, self._projs[proj]):
        self._projs[proj][0] = Project.STATE_OK
    # Process pages
    need_hierwrite = 0
    for page, exts in self._pages.items():
      if progress:
        progress.set_text('Resolving %s' % page).write().incr(
            sum(1 for ext in exts if ext[0] not in Project.STATES_NEED_MERGE))
      # Handle the easy ones
      for ext in exts:
        if self._git_pick('%s.%s' % (page, ext), exts[ext]):
          exts[ext][0] = Project.STATE_OK
      # Handle the harder ones, if we're allowed to
      if not (mode & Project.MODE_REWRITE):
        continue
      IS_OK = [Project.STATE_OK]
      # Merge the pgFile
      if exts.get('pgFile', IS_OK)[0] in Project.STATES_NEED_MERGE:
        if (exts.get('pgFile', IS_OK)[0] == Project.STATE_USER
            or (mode & Project.MODE_FORCE_UI) == Project.MODE_FORCE_UI):
          # Collect *all* of the pages that need user intervention
          if not self.resolve_ui(mode, progress) and mode & Project.MODE_UI:
            raise Exception('unexpected: resolve_ui did nothing')
        else:
          files = []
          for f in git.cat_files('%s.pgFile' % page):
            files.append(filetypes.pgFile.pgFile(f, fname=str(f.name)))
          # _determine_three_way merges base (files[0]) for us
          if Project._determine_three_way(files) != Project.STATE_BOTH:
            raise Exception('unexpected: _determine_three_way was inconsistent')
          files[0].set_timestamp(time.strftime('%c'))
          with open(git.repo_path('%s.pgFile' % page), 'wb') as f:
            files[0].write(f)
          if git.add(git.repo_path('%s.pgFile' % page)):
            exts['pgFile'][0] = Project.STATE_OK
          if progress:
            progress.incr()
      # TODO: removed CAD-tool file specific code, rewrite
    # Rewrite databases. First do ones that are independent.
    for database in self._databases:
      # TODO: Add code to look at different types of databases and rewrite
      continue
    # Now do the interdependent databases
    for database in self._databases:
      # TODO: Add code to look at different types of databases and rewrite
      continue
    # Rewrite hierarchy. We can't rewrite if there are unmerged files
    if need_hierwrite and self.state() == Project.STATE_OUT_OF_DATE:
      curproj = 1
      for proj in self._projs:
        if progress:
          ptext = (' (%d of %d)' % (curproj, len(self._projs))
                   if len(self._projs) > 1 else '')
          progress.set_text('Writing hierarchy of%s %s' % (ptext, proj)).write()
          curproj += 1
        for block, page, extension in self._hierwrite(proj, progress):
          # Invalid output (see _hierwrite docstring)
          if page and not extension:
            continue
          path = 'path/to/path' % (
              os.path.dirname(proj), block, page or block)
          for ext in [extension] if extension else ('list', 'of', 'file', 'extensions'):
            pathext = '%s.%s' % (path, ext)
            if not git.add(pathext):
              continue
            if page:
              self._pages.get(path, {}).get(ext, [0])[0] = Project.STATE_OK
            elif pathext in self._databases:
              self._databases[pathext][0] = Project.STATE_OK
    if need_hierwrite and progress:
      progress.incr(need_hierwrite)


  def resolve_generic(self, filename, files, mode=MODE_SAFE, progress=None):
    """Updates files[0] by merging files[1] and files[2]. Works for any file.
    If there are conflicts and the mode allows it, asks the user to resolve
    them in the console. There is no knowledge of the filetype to create context
    for the user, so this isn't an ideal way of asking the user for input.
    """
    conflicts = []
    files = list(files)
    if git.is_rebase():
      files = [files[0], files[2], files[1]]
    conflicts = diff.threeway(files[0], files[1], files[2])
    if not conflicts:
      return Project.STATE_BOTH
    if (mode & Project.MODE_ALL != Project.MODE_ALL
          or not os.isatty(0) or not os.isatty(2)):
      return Project.STATE_CONFLICT
    if progress:
      progress.clear()
    sys.stderr.write('Resolving %d conflict%s in %s:\n' % (
      len(conflicts), 's' if len(conflicts) > 1 else '', filename))
    for conflict in conflicts:
      sys.stderr.write('%s\n' % diff.conflicts_to_str([conflict]))
      resp = ''
      while not resp.startswith(('o', 't')):
        sys.stderr.write('Keep [o]urs or [t]heirs? > ')
        sys.stderr.flush()
        resp = sys.stdin.readline().lower()
      if diff.applylists(conflict[0 if resp[0] == 'o' else 1]):
        raise Exception('failed to apply diffs')
    return Project.STATE_BOTH


  def resolve_ui(self, mode=MODE_SAFE, progress=None):
    """Presents a UI for resolving all manually-resolvable conflicts at once.
    If provided, advances progress just once.
    Returns the number of files handled.
    """
    if mode & Project.MODE_ALL != Project.MODE_ALL:
      return 0
    base_pgFiles = []
    for page, exts in self._pages.items():
      IS_OK = [Project.STATE_OK]
      # Collect pgFiles
      if (exts.get('pgFile', IS_OK)[0] != Project.STATE_USER and
          (exts.get('pgFile', IS_OK)[0] != Project.STATE_BOTH
           or mode & Project.MODE_FORCE_UI != Project.MODE_FORCE_UI)):
        continue
      base_pgFiles.append([None, page, exts])
    if not base_pgFiles:
      return 0
    ui = DiffUI(mode=DiffUI.MODE_MERGE,
        title='webschviewdiffmerge: resolving conflicts in %s' % (
              self._project.rstrip('/\\')))
    for base_pgFile in base_pgFiles:
      page = base_pgFile[1]
      exts = base_pgFile[2]
      if progress:
        progress.set_text('Preparing merge UI for %s' % page).write()
      datadir = page
      while datadir and os.path.basename(datadir) != 'datadir':
        datadir = os.path.dirname(datadir)
      pgFiles = list(map(filetypes.pgFile.pgFile, git.cat_files('%s.pgFile' % page)))
      # For the purposes of a UI, always show ours first (flip ours/theirs)
      if git.is_rebase():
        pgFiles[1], pgFiles[2] = pgFiles[2], pgFiles[1]
      base_pgFile[0] = pgFiles[0]
      diffs = []
      conflicts = diff.threeway(pgFiles[0], pgFiles[1], pgFiles[2], return_safe=diffs)
      ui.addpage(page, pgFiles[0], diffs, conflicts,
                 datadir=git.repo_path(datadir))
    if progress:
      progress.set_text('Waiting for user to complete merge').write()
    selected = ui.getresponse()
    if selected is None:
      raise KeyboardInterrupt('3-way merge cancelled by user')
    if progress:
      progress.set_text('Writing merge result').incr().write()
    if diff.applylists(selected):
      raise Exception('conflicting diffs selected')
    timestamp = time.strftime('%c')
    for pgFile, page, exts in base_pgFiles:
      pgFile.set_timestamp(timestamp)
      with open(git.repo_path('%s.pgFile' % page), 'wb') as f:
        pgFile.write(f)
      if git.add(git.repo_path('%s.pgFile' % page)):
        if (page, 'pgFile') in self._conflicts:
          del self._conflicts[(page, 'pgFile')]
        exts['pgFile'][0] = Project.STATE_OK
    return len(base_pgFiles)

  def determine_file_states(self, progress=None):
    """Processes all unresolved files, determining resolution approach."""
    self._conflicts = {}
    for page, exts in self._pages.items():
      if progress:
        progress.set_text('Checking %s' % page).write().incr(len(exts))
      pagedir = os.path.dirname(page)
      handled = set()
      # Handle schematic and schematic-derived files
      if 'pgFile' in exts:
        path = '%s.pgFile' % page
        files = [filetypes.pgFile.pgFile(f, fname=path) if f else None
                 for f in git.cat_files(path, exts['pgFile'][1])]
        state = Project._determine_three_way(files,
            ret_conflicts=self._conflicts.setdefault((page, 'pgFile'), []))
        # Conflicts in pgFile files can be resolved by users
        state = Project.STATE_USER if state == Project.STATE_CONFLICT else state
        exts['pgFile'][0] = state
        handled.update({'pgFile'})
     # TODO: deleted code dealing with cad specific file types
      # Anything not handled should be marked as unresolvable
      for ext in exts.keys() - handled:
        exts[ext][0] = Project.STATE_CONFLICT
    self._add_projs()
    for path, proj in self._projs.items():
      if progress:
        progress.set_text('Checking %s' % path).write().incr()
      if proj[0] == Project.STATE_OK:
        continue
      files = [filetypes.proj.proj(f) if f else None
               for f in git.cat_files(path, proj[1])]
      proj[0] = Project._determine_three_way(files)
    for path, database in self._databases.items():
      try:
        path = path.decode()
      except (UnicodeDecodeError, AttributeError):
        pass
      if progress:
        progress.set_text('Checking %s' % path).write().incr()
      else:
        raise Exception('Unhandled database: %s\n' % path)


  def _hierwrite(self, proj, progress=None):
    """Saves project hierarchy and yields (block, page, extension) per file.
    block: the block written
    page: the page written (e.g. 'page1'), or None for databases
    extension: the extension of the file written, or None for databases
               Ignore output if page is not None and extension is None.
    Duplicate output is possible.
    
    TODO: This code is very tool specific and was removed. modify so it's more generic
    """
    
  def _netlister(self, proj, progress=None):
    """Runs netlister on a proj and returns blocks netlisted.
        TODO: This code is very tool specific and was removed. modify so it's more generic
    """
    
  def _git_pick(self, path, state):
    """Resolves a conflict by simply picking a version via git checkout.
    Fails on merges, and handles OUT_OF_DATE by picking the remote version.
    """
    state = state[0]
    if state == Project.STATE_OK:
      return True
    if state in Project.STATES_NEED_MERGE + Project.STATES_UNRESOLVABLE:
      return False
    if state == Project.STATE_OURS:
      version = git.VERSION_OURS
    elif state == Project.STATE_THEIRS:
      version = git.VERSION_THEIRS
    else:
      # Default to the version on remote to minimize diffs.
      # In the case of a rebase, this is (counter-intuitively) "ours"
      version = git.VERSION_OURS if git.is_rebase() else git.VERSION_THEIRS
    if git.checkout(path, version) and git.add(path):
      return True
    return False

  @staticmethod
  def _determine_three_way(base, ours=None, theirs=None, ret_conflicts=None):
    """Performs a three-way comparison of a single conflicting file in git.
    base   -- object representing the base version of the file, or a list of
              three objects to compare if ours and theirs are None.
              The base object *will get modified* if it is an instance of
              Comparable and it's determined that there are conflicts.
    ours   -- object representing the git --ours version of the file.
    theirs -- object representing the git --theirs version of the file.
    """
    if ours is None:
      base, ours, theirs = base
    # Don't even try to resolve a conflict that involves a missing file
    if any(x is None for x in (base, ours, theirs)):
      return Project.STATE_CONFLICT
    if base == ours:
      if base == theirs:
        return Project.STATE_OUT_OF_DATE
      else:
        return Project.STATE_THEIRS
    elif ours == theirs:
      return Project.STATE_OUT_OF_DATE
    elif base == theirs:
      return Project.STATE_OURS
    elif isinstance(base, diff.Comparable):
      # Perform a three-way merge. THIS MODIFIES base
      conflicts = diff.threeway(base, ours, theirs)
      # Debug: output conflicts
      if conflicts and ret_conflicts is not None:
        ret_conflicts += conflicts
      # If we succeeded, mark the file with BOTH
      return Project.STATE_CONFLICT if conflicts else Project.STATE_BOTH
    return Project.STATE_CONFLICT