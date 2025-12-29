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
import importlib
import os
import sys
from abc import ABC, abstractmethod
from collections import namedtuple
from copy import deepcopy

DiffParam = namedtuple("DiffParam", ["v", "c"])


class Comparable(ABC):
  """Superclass that simplifies implementation of comparables."""

  @abstractmethod
  def __eq__(self, other):
    """Checks for equality, ideally quickly."""

  def sortkey(self):
    """Default implementation of sortkey is just the string representation."""
    return repr(self)

  def diff(self, other, _=None):
    """Returns a list of differences the other has. Akin to (other - self).
    Returns an empty list if the two are the same.
    Returns None if the two are disparate (shouldn't be compared).
    Default implementation returns an empty list if equal, and None otherwise.
    """
    return [] if self == other else None

  def distance(self, other, fast, diffparam=None):
    """Calculates a distance metric for the diff with another object.
    This is a number of arbitrary scale, where 0 means definite match (but may
    still have changes) and increasing positive numbers mean reduced
    similarity.  Returns None if the two are disparate (shouldn't be
    compared).
    If fast is True, magnitude does not matter; just consider zero or non-zero.
    Default implementation uses the number of items in the diff. This will
    cause multiple evaluations of diff() unless overridden.
    """
    # Avoid the diff in the case of "fast"
    if fast:
      return 1 * (not self.__eq__(other))
    diff = self.diff(other, diffparam)
    return None if diff is None else len(diff)

  @abstractmethod
  def apply(self, key, data):
    """Applies a single difference. apply(d) for d in diff(other) => other
    Return an error string if the patch could not be applied due to conflict.
    Return True if the diff was redundant.
    key:  the key provided during instantiation of Diff
    data: One of a few things, depending on the type of diff.
          tuple(old, None) -> removal of "old" (value or instance)
          tuple(None, new) -> addition of "new" (value or instance)
          tuple(old, new) -> change from "old" to "new" (value)
    Default implementation handles keys with properties in self.PROPS.
    """

  def child_is_deleted(self, _child):
    """Used to check if the diff's target was deleted.
    If this function returns true, a conflict is raised if the change is
    important.  Only used when evaluating a diff list.
    """
    return False

  def param(self, diffs, key, base):
    """Convenience function to return a param even if no diffs are available."""
    return TargetDict.param(diffs, self, key, base)


class Param:
  """Tracks zero or more diffs as a single parameter."""

  def __init__(self, func, *args, default=None):
    """Tracks a lazy-eval parameter, based on constants, diffs, or other Params.
    func  -- a function to lazy-apply to args, or a constant value for the Param
    *args -- one or more arguments, Params, or list[Diff]s.
             The resulting svg class is a union of any diffs included.
    default -- returned whenever there is no value or a function returns None
    """
    assert callable(func) == bool(args), "missing either function or args"
    if isinstance(func, Param):
      # Shallow copy, since Param operations are idempotent
      assert not args
      self._args = func._args
      self._func = func._func
      self._lencache = func._lencache
      self._evalcache = func._evalcache
      self._default = func._default if default is None else default
      return
    if not args:
      assert not isinstance(func, Diff)
      self._args = [func]
      self._func = None
    else:
      if len(args) == 1 and isinstance(args[0], Param) and not args[0]._func:
        # Copy-through
        self._args = args[0]._args
      else:
        assert not any(isinstance(a, list) for a in args), "ambiguous arg type"
        assert not any(isinstance(a, Diff) for a in args), "arg not Diff.Group"
        self._args = args
      self._func = func
    self._default = default
    self._lencache = 1
    for arg in self._args:
      if isinstance(arg, (Param, Diff.Group)):
        self._lencache = max(self._lencache, len(arg))
    self._evalcache = {}

  def __str__(self):
    return (
      f"<Param({self._func}, "
      f"*({', '.join(map(str, self._args))})[:{len(self)}])>"
    )

  @staticmethod
  def multi(count, func, *args):
    """Returns a tuple of Params that provides the result of a function applied
    to the args. Function must return a tuple of length count.
    """
    intermediate = Param(func, *args)
    return tuple(intermediate.map(lambda x, i: x[i], i) for i in range(count))

  @staticmethod
  def array(*items, array=None):
    """Constructs a Param that combines params into an array.
    Long-term, this should be avoided since it conflates many diffs into one.
    """
    return Param(lambda *a: a, *(array or items))

  def __getitem__(self, i):
    """Indexes into the diffs and returns a DiffParam of (value, svgclassset).
    raises IndexError if the index is out of bounds.
    Caches calculations.
    """
    if isinstance(i, slice):
      ret = []
      for j in range(*i.indices(len(self))):
        ret.append(self[j])
      return ret
    if not (0 <= i < len(self)):
      raise IndexError(i)
    if i not in self._evalcache:
      args = []
      svgclasses = set()
      for arg in self._args:
        if isinstance(arg, Param):
          arg = arg.get(i)
        elif isinstance(arg, Diff.Group):
          diff_i = min(len(arg), i)
          arg = arg[diff_i]
          if isinstance(arg, Diff):
            arg = arg.forsvg()
        if isinstance(arg, DiffParam):
          args.append(arg.v)
          svgclasses.update({arg.c} if isinstance(arg.c, str) else arg.c)
        else:
          args.append(arg)
      assert self._func or len(args) == 1
      self._evalcache[i] = ret = DiffParam(
        v=self._func(*args) if self._func else args[0],
        c=svgclasses,
      )
    else:
      ret = self._evalcache[i]
    return self._default if ret is None else ret

  @property
  def v(self):
    """Convenience getter for code that doesn't care about diffs."""
    return self[0].v

  def get(self, i):
    """Clamps i to the last diff in the set."""
    return self.__getitem__(min(len(self) - 1, i))

  def __len__(self):
    return self._lencache

  def map(self, func, *args):
    """Returns a Param that provides the result of a function applied to this
    plus other args.
    Function will be passed self and *args
    """
    return Param(func, self, *args)

  def reduce(self, func, innerfunc=None):
    """Returns the result of a function applied across len(self)
    Function will be passed all of the versions as an iterable.
    If innerfunc is provided, apply to each value before passing to func.
    """
    return func(innerfunc(x.v) if innerfunc else x.v for x in self)

  @staticmethod
  def ify(param, default=None, diffs=None, key=None):
    """Ensures param is a Param-like object."""
    if param is None:
      return Param(default)
    if hasattr(param, "param"):
      return param.param(diffs=diffs, key=key, default=default)
    return Param(param, default=default)


class Diff:
  """Tracks a single difference (or nested list of Diffs)."""

  APPLY_IMPORTANT = 1 << 0
  APPLY_UNIMPORTANT = 1 << 1
  APPLY_FORCEIMPORTANT = 1 << 2
  APPLY_ALL = APPLY_IMPORTANT | APPLY_UNIMPORTANT
  APPLY_FORCEALL = APPLY_ALL | APPLY_FORCEIMPORTANT

  class Group(list):
    """A matching set of diffs in an n-way diff"""

    def __init__(self, *entries):
      if len(entries) == 1 and isinstance(entries[0], Diff):
        entries = (entries[0]._data[0], entries[0])
      assert all(isinstance(e, Diff) for e in entries[1:]), entries
      super().__init__(entries)

  # datatypes:
  # add (None, elem)
  # rm (elem, None)
  # mod (oldval, newval)
  # mod [list of diffs]
  def __init__(self, target, key, diffs=None, old=None, new=None):
    """Records the diff.
    target: The instance responsible for apply()ing the diff later.
            Can be a tuple of (instance, class) if the target is a superclass.
    key:    Arbitrary key passed to apply() for applying the diff. Can be None.

    The actual change is specified via the positional arguments:
    diffs:  A single Diff or list of diffs that apply to children of the target.
    old:    The old value or instance. Indicates a change if new is also
            specified. Indicates removal if new is not specified.
            A deep copy will be made of this instance to aid in conflict
            detection.  Override __deepcopy__ to modify the copy behavior.
            NOTE: Shallow copy vs deep copy only matters in the case of a
            modify+delete conflict, when the object contains a sub-object,
            dictionary or list.  Examples include wires with properties, and
            labels with display properties.  The question is if one person
            deletes a wire and the other person changes the wire's signal name
            text color, is it a conflict?  What if the other person changed the
            signal name itself?  Instead of prescribing the behavior by choosing
            shallow copy, depend on the equality comparison step of "old" to
            determine if the delete is a conflict.
    new:    The new value or instance. Indicates an add if old isn't specified.
    """
    if isinstance(target, tuple):
      self._target, self._target_class = target
    else:
      self._target = target
      self._target_class = target.__class__
    self._key = key
    if old is not None or new is not None:
      self._data = (deepcopy(old), new)
      if old is None:
        self._description = "add"
      elif new is None:
        self._description = "rm"
      else:
        self._description = "mod"
    elif diffs is not None:
      if isinstance(diffs, Diff):
        diffs = [diffs]
      elif not isinstance(diffs, list):
        raise Exception("use positional arguments when making Diff objects")
      for diff in diffs:
        diff._parent = self
      self._data = diffs
      self._description = "mod"
    else:
      self._data = None
      self._description = "changed"
    self._oid = None if old is None else id(old)
    self._svgclass = f"diff{id(self):X}"
    self._parent = None
    self._redundant = False
    self._rendered = False
    self._unimportant = False

  @property
  def is_add(self):
    return isinstance(self._data, tuple) and self._data[0] is None

  @property
  def is_rm(self):
    return isinstance(self._data, tuple) and self._data[1] is None

  @property
  def old_id(self):
    """The id of the original object being modified.
    Useful for finding it in some container.
    """
    return self._oid

  def is_instance(self, cls):
    """Checks if either the old or new objects are an instance of a class.
    Does not flag the diff as rendered.
    """
    return isinstance(self._data, tuple) and (
      cls is None
      or isinstance(self._data[0], cls)
      or isinstance(self._data[1], cls)
    )

  def is_unimportant(self, applymode=0):
    """Returns True if the change is unimportant, or if it exists of changes
    that are all unimportant.
    A conflicting, unimportant Diff can be merged by picking one arbitrarily, or
    dropped entirely.
    applymode -- if Diff.APPLY_FORCEIMPORTANT is set, always returns False
    """
    if applymode & Diff.APPLY_FORCEIMPORTANT:
      return False
    if self._unimportant:
      return True
    if isinstance(self._data, list):
      return all(map(Diff.is_unimportant, self._data))
    return False

  def set_unimportant(self, unimportant=True):
    """Flags (or unflags) the change as unimportant."""
    self._unimportant = unimportant

  def should_be_applied(self, mode):
    """Checks if this diff should be applied under the provided mode."""
    if self.is_unimportant():
      return mode & Diff.APPLY_UNIMPORTANT != 0
    return mode & Diff.APPLY_IMPORTANT != 0

  def apply(self, mode=APPLY_ALL):
    """Applies the diff.
    mode: OR'd combination of Diff.APPLY_ flags
    """
    # Handle lists regardless of mode
    if isinstance(self._data, list):
      conflicts = []
      for diff in self._data:
        if self._target_class.child_is_deleted(self._target, diff._target):
          if diff.should_be_applied(mode) and diff.is_unimportant(mode):
            diff.set_redundant()
          else:
            conflicts.extend(diff._flatten(mode))
        else:
          conflicts.extend(diff.apply(mode))
      return conflicts
    # Only apply selected diffs (don't FORCEIMPORTANT yet)
    if not self.should_be_applied(mode):
      return []
    # Handle normal diffs
    conflict = self._target_class.apply(self._target, self._key, self._data)
    if conflict is None:
      return []
    elif conflict is True or self.is_unimportant(mode):
      self.set_redundant()
      return []
    elif isinstance(conflict, list):
      raise Exception("Comparable.apply should never return a list of diffs")
    else:
      return [self]

  def forsvg(self):
    """Returns a DiffParam of the applied diff, including the value and an
    SVG-compatible class name set associated with this diff.
    """
    if isinstance(self._data, list):
      raise Exception("diff lists are not renderable")
    self._rendered = True
    return DiffParam(self._data[1], self._svgclass)

  def is_redundant(self):
    """Returns True if the change is redundant, or if it exists of changes
    that are all redundant.
    A redundant Diff is one that is conflicting but either has the same final
    result as previously-applied Diffs or is marked as unimportant.
    This Diff is not applied, and that's A-OK.
    """
    if self._redundant:
      return True
    if isinstance(self._data, list):
      return all(map(Diff.is_redundant, self._data))
    return False

  def set_redundant(self, redundant=True):
    """Flags (or unflags) the change as redundant with another change."""
    self._redundant = redundant

  def redundant_with(self, other):
    """Returns true if the diff is definitely redundant with another diff.
    Even if this returns false, the diff may still be redundant (delete-modify).
    """
    if isinstance(self._data, list):
      return False
    if self._target is not other._target or self._key != other._key:
      return False
    if (self._data[0] is None) != (other._data[0] is None):
      return False
    if (self._data[1] is None) != (other._data[1] is None):
      return False
    return self._data == other._data

  def svgrendered(self, rendered=None):
    """Returns whether the svg was rendered, and, if specified, overrides the
    internal value.  Overriding is useful if the render got pruned for whatever
    reason.
    """
    if rendered is not None:
      self._rendered = rendered
    return self._rendered

  def _flatten(self, applymode=APPLY_ALL):
    """Returns a flat list of diffs, even if they are nested.
    applymode -- optionally filters out diffs that shouldn't be applied yet
    """
    if not isinstance(self._data, list):
      return [self] * self.should_be_applied(applymode)
    flattened = []
    for diff in self._data:
      flattened += diff._flatten(applymode)
    return flattened

  def _target_str(self):
    """Returns a human-readable description of the diff target."""
    fields = (
      self._parent._target_str() if self._parent else "",
      str(self._target) + ":",
      self._description,
      self._key,
    )
    return " ".join(s for s in fields if s)

  def __str__(self):
    """Human-readable representation of the diff."""
    if isinstance(self._data, list):
      return "\n".join(map(str, self._data))
    return " ".join(
      (
        self._target_str(),
        " => ".join(data_to_str(x) for x in self._data or [] if x is not None),
      )
    )


class FakeDiff(Diff):
  """Fakes a diff for various useful purposes.
  One common use is if you want a custom param with a specific class.
  """

  def __init__(self, cls, *args, **kargs):
    super().__init__(None, None, *args, **kargs)
    self._svgclass = cls

  def param(self):
    return Param(Diff.Group(self))


def data_to_str(x):
  if isinstance(x, (list, tuple)):
    if len(x) > 1:
      g = ((z if len(z) <= 10 else f"{z:.7}...") for y in x for z in (str(y),))
      ret = f"({', '.join(g)})"
      return ret if len(ret) <= 30 else f"{ret:.27}..."
    return str(x[0])
  return str(x)


def _minmatrix(matrix):
  """Returns the x,y of the smallest non-None element of the matrix, or None."""
  best = None
  bestval = None
  for i in range(len(matrix)):
    if matrix[i] is None:
      continue
    for j in range(len(matrix[i])):
      if matrix[i][j] is None:
        continue
      thisval = matrix[i][j]
      if bestval is None or thisval < bestval:
        best = (i, j)
        bestval = thisval
  return best


def _flatten(diffs):
  if diffs is None:
    return
  if isinstance(diffs, Diff):
    for d in diffs._flatten():
      yield d
  else:
    for diff in diffs:
      for d in _flatten(diff):
        yield d


class TargetDict(dict):
  def __init__(self, difflist):
    """Takes a list of diffs and returns a dict mapping (target, key) to a list
    of diffs.  The diffs are flattened, so diffs that contain a list of diffs
    will be expanded. Since mapping is based on the id of target, deepcopy will
    not properly update the mappings, so deepcopy will raise an exception to
    warn if this happens.
    """
    for diff in _flatten(difflist):
      target = diff._target
      if isinstance(target, tuple):  # target, class
        target = target[0]
      self.setdefault((id(target), diff._key), []).append(diff)

  def __deepcopy__(self, _):
    raise Exception("Deepcopy not possible")

  def get(self, target, key, default=None):
    return super().get((id(target), key), default)

  def param(self, target, key, base, default=None):
    diffs = None if self is None else self.get(target, key)
    return Param(Diff.Group(base, *(diffs or ())), default=default)


def matchlists(base, other, data=None):
  """Matches two lists of comparables by distance even if the order has changed.
  Best case O(N) (similar sorting), worst-case O(N^2) (random sorting).
  Returns a list in the same order as base, where each element is the
  corresponding comparable in other, or None if base was removed.
  Additional comparables beyond len(base) are new additions.
  """
  # Determine the list of differences between every pair of params
  base_matches = [None] * len(base)
  other_matched = set()
  matrix = []
  # Fill in the matrix of diffs. If we come across an exact match, record it
  # to clear it out of the matrix.
  for i in range(len(base)):
    matrix.append([None] * len(other))
    for j in range(len(other)):
      if j in other_matched:
        continue
      matrix[i][j] = base[i].distance(other[j], True, data)
      # Detect the exact match and remove it from the equation
      if matrix[i][j] == 0:
        base_matches[i] = j
        other_matched.add(j)
        matrix[i] = None
        for k in range(i):
          if matrix[k] is not None:
            matrix[k][j] = None
        break
  # Anything that's left in the matrix doesn't have an exact match; fill in
  # distance.
  for i in range(len(base)):
    if matrix[i] is None:
      continue
    for j in range(len(other)):
      if matrix[i][j] is not None:
        matrix[i][j] = base[i].distance(other[j], False, data)
      # Detect the exact match and remove it from the equation
      if matrix[i][j] == 0:
        base_matches[i] = j
        other_matched.add(j)
        matrix[i] = None
        for k in range(len(base)):
          if k != i and matrix[k] is not None:
            matrix[k][j] = None
        break
  # Keep the pairings that have the shortest number of differences
  while True:
    best = _minmatrix(matrix)
    if best is None:
      break
    base_matches[best[0]] = best[1]
    other_matched.add(best[1])
    matrix[best[0]] = None
    for row in matrix:
      if row is not None:
        row[best[1]] = None
  # Summarize matches (modifications) and lack of matches (removals)
  matches = [None if match is None else other[match] for match in base_matches]
  # Any indices not matched in other have been added
  matches += [other[adds] for adds in set(range(len(other))) - other_matched]
  return matches


def difflists(target, key, base, other, data=None):
  """Diffs two lists, trying to keep things matched even if the order has
  changed. Complexity is set by matchlists() implementation above.
  Returns a list of Diffs, instantiated with target and key.
  """
  # Determine the list of differences between every pair of params
  base_matches = matchlists(base, other, data)
  # Now collect the changes. List adds first.
  diff = [Diff(target, key, new=add) for add in base_matches[len(base) :]]
  # Handle matches (modifications) and lack of matches (removals) in self
  for old, new in zip(base, base_matches):
    if new is None:
      diff.append(Diff(target, key, old=old))
      continue
    subdiff = old.diff(new, data)
    if subdiff:
      diff.append(Diff(target, key, diffs=subdiff))
  return diff


def _applylist(difflist, mode=Diff.APPLY_ALL):
  """Applies a list of Diffs and returns a list of conflicting Diffs.
  mode -- an OR of Diff.APPLY_* options
  """
  conflicts = []
  for diff in difflist:
    conflicts.extend(diff.apply(mode))
  return conflicts


def applylists(difflistlist):
  """Applies a list of lists of Diffs, doing all of the important diffs first
  and following up with unimportant diffs.
  """
  if difflistlist and isinstance(difflistlist[0], Diff):
    difflistlist = [difflistlist]
  conflicts = []
  for difflist in difflistlist:
    conflicts.extend(_applylist(difflist, Diff.APPLY_IMPORTANT))
  for difflist in difflistlist:
    if _applylist(difflist, Diff.APPLY_UNIMPORTANT):
      raise Exception("unimportant diffs shouldn't conflict")
  return conflicts


def _determine_association(state, pairs, associate_redundant_diffs):
  """Test-applies diffpairs to determine matching diffs."""
  # For each theirs conflict, determine associated ours diffs
  for ours_indices, theirs_indices in pairs:
    # There is only one index in theirs_indices at the moment
    theirs_index = theirs_indices.pop()
    if associate_redundant_diffs:
      # It's much faster to try to associate redundant diffs based on
      # equivalence, so try that first.
      ours_indices.update(
        i
        for i in range(len(state["dours_flat"]))
        if state["dtheirs_flat"][theirs_index].redundant_with(
          state["dours_flat"][i]
        )
      )
    if not ours_indices:
      # Do a new round of applying diffs, first applying the theirs conflict, in
      # order to find the corresponding ours conflicts
      state_copy = deepcopy(state)
      if state_copy["dtheirs_flat"][theirs_index].apply():
        raise Exception("unexpected: failed to apply theirs diff")
      # Force unimportant diffs to be conflicting so that they're grouped with
      # the other actually conflicting diffs.
      conflicts = _applylist(state_copy["dours"], Diff.APPLY_FORCEALL)
      if associate_redundant_diffs:
        ours_indices.update(
          i
          for i in range(len(state_copy["dours_flat"]))
          if state_copy["dours_flat"][i].is_redundant()
        )
        if not ours_indices and not conflicts:
          theirs_indices.add(theirs_index)
          continue
      elif not conflicts:
        raise Exception("unexpected: failed to find ours conflicts for diff")
      else:
        # Map the conflicts into flattened ours list indices
        ours_indices.update(
          state_copy["dours_flat"].index(d) for d in conflicts
        )
    # If any of these indices show up in previous pairs, combine with that pair
    for prev_ours_indices, prev_theirs_indices in pairs:
      if ours_indices.isdisjoint(prev_ours_indices):
        continue
      # If no other matches, prev_ours_indices will eventually be ours_indices
      prev_ours_indices.update(ours_indices)
      prev_theirs_indices.add(theirs_index)
      break
    else:
      raise Exception("unexpected: failed to find ours_indices in the list")
  # Convert back into original diff references and remove empty pairs
  return [
    (
      [state["dours_flat"][d] for d in p[0]],
      [state["dtheirs_flat"][d] for d in p[1]],
    )
    for p in pairs
    if p[1]
  ]


def threeway(base, ours, theirs, return_safe=None):
  """Does a three-way merge of base, ours, and theirs, updating 'base' with all
  non-conflicting changes applied, and returning a (possibly empty) list of
  tuple(ours, theirs), where ours and theirs are lists of conflicting diffs.
  If return_safe is a list, fills the list with safe diffs (same format as
  the return list) instead of applying them, with redundant diffs paired
  together.
  """
  # Encapsulate base and diffs into a dict so we can deep-copy
  state = {
    "base": base,
    "dours": base.diff(ours),
    "dtheirs": base.diff(theirs),
    "dours_flat": [],
    "dtheirs_flat": [],
  }
  for diff in state["dours"]:
    state["dours_flat"] += diff._flatten()
  for diff in state["dtheirs"]:
    state["dtheirs_flat"] += diff._flatten()
  # Do a trial run of the merge to capture any conflicts in theirs
  state_copy = deepcopy(state)
  if _applylist(state_copy["dours"], Diff.APPLY_IMPORTANT):
    raise Exception("unexpected: failed to apply ours diff")
  # Force unimportant diffs to be conflicts now, so sets of conflicting commits
  # are grouped better.  We'll pull artificial conflicts out later.
  # Apply unimportant dours after the important theirs, though.
  conflicts = _applylist(state_copy["dtheirs"], Diff.APPLY_IMPORTANT)
  if _applylist(state_copy["dours"], Diff.APPLY_UNIMPORTANT):
    raise Exception("unexpected: unimportant diffs should not conflict")
  conflicts += _applylist(
    state_copy["dtheirs"], Diff.APPLY_UNIMPORTANT | Diff.APPLY_FORCEIMPORTANT
  )
  # Map the conflicts into flattened theirs list indices
  pairs = [(set(), {state_copy["dtheirs_flat"].index(d)}) for d in conflicts]
  pairs = _determine_association(state, pairs, False)
  # Generate list of safe changes
  dours_safe = [
    d for d in state["dours_flat"] if all(d not in p[0] for p in pairs)
  ]
  dtheirs_safe = [
    d for d in state["dtheirs_flat"] if all(d not in p[1] for p in pairs)
  ]
  # Pull out the conflicts that are actually OK because nothing is important
  safe_pairs = [
    p
    for p in pairs
    if all(d.is_unimportant() for d in p[0])
    or all(d.is_unimportant() for d in p[1])
  ]
  pairs = [p for p in pairs if p not in safe_pairs]
  # Return list of safe diffs if requested
  if isinstance(return_safe, list):
    # Pair up diffs that are redundant
    safe_pairs_i = [
      (set(), {i})
      for i in range(len(state_copy["dtheirs_flat"]))
      if state_copy["dtheirs_flat"][i].is_redundant()
      and state["dtheirs_flat"][i] in dtheirs_safe
    ]
    safe_pairs += _determine_association(state, safe_pairs_i, True)
    return_safe += [
      ([d], []) for d in dours_safe if all(d not in p[0] for p in safe_pairs)
    ]
    return_safe += [
      ([], [d]) for d in dtheirs_safe if all(d not in p[1] for p in safe_pairs)
    ]
    # Provide the redundant pairs last; makes for better UI
    return_safe += safe_pairs
  else:
    # Apply all the diffs that aren't conflicting and return
    if applylists((_flatten(safe_pairs), dours_safe, dtheirs_safe)):
      raise Exception("unexpected: failed to apply safe diffs")
  return pairs


def conflicts_to_str(conflicts):
  """Converts a list of conflicts or pairs of conflicts to string."""
  if not conflicts:
    return ""
  if isinstance(conflicts, dict):
    return "\n".join(conflicts_to_str(c) for c in conflicts.values() if c)
  elif isinstance(conflicts[0], tuple):
    return "\n".join(
      "\n".join(
        (
          f"{'=' * 35} CONFLICT {'=' * 35}",
          "\n".join(f"  OURS: {c}" for c in ours_conflicts),
          "\n".join(f"THEIRS: {c}" for c in theirs_conflicts),
        )
      )
      for ours_conflicts, theirs_conflicts in conflicts
    )
  return "\n".join(f"CONFLICT: {c}" for c in conflicts)


def main(argv):
  """USAGE: diff.py a b
  Reads files a and b and produces a 2-way diff.
  Exits with code 0 if the files are the same, 1 if there are differences.
  """

  from .kicad_common import Drawable
  from .sexp import parse
  from .svg import Svg

  parser = argparse.ArgumentParser(
    prog="kischvidimer diff",
    description="""Generates a diff""",
  )
  parser.add_argument(
    "-q",
    "--quiet",
    action="store_true",
    help="silences diff output",
  )
  parser.add_argument(
    "-s",
    "--svg",
    action="store_true",
    help="output an SVG rather than a diff list",
  )
  parser.add_argument(
    "--selector",
    help="only diff on a subset of the file",
  )
  parser.add_argument(
    "base",
    help="base kicad file to compare",
  )
  parser.add_argument(
    "target",
    help="target kicad file to compare",
  )
  args = parser.parse_args(argv[1:])
  sels = args.selector.split(":") if args.selector else []

  files = []
  invert_y = False
  for path in args.base, args.target:
    ext = os.path.splitext(path)[1]
    if not ext:
      raise ValueError(path)
    if ext == ".kicad_sym":
      invert_y = True
    mod = importlib.import_module(ext, __package__)
    with open(path) as f:
      if ext[1:] in mod.__dict__:
        parsed = mod.__dict__[ext[1:]](f, fname=path)
      else:
        parsed = parse(f.read())[0]
      if sels and "(" in sels[0]:
        parsed = eval(f"parsed.{sels[0]}")
      elif sels and sels[0].startswith("["):
        parsed = eval(f"parsed{sels[0]}")
      files.append(parsed)
  diffs = files[0].diff(files[1])
  if args.svg:
    s = Svg(theme="default")
    s.push_invert_y(invert_y)
    files[0].fillsvg(
      s,
      TargetDict(diffs),
      Drawable.DRAW_ALL,
      (),
      **dict(s.partition("=")[::2] for s in sels if "=" in s and "(" not in s),
    )
    print(str(s))
  if not args.quiet:
    difftext = f"Diffs from {args.base} to {args.target}:\n"
    difftext += "\n".join(map(str, diffs))
    if args.svg:
      print(f"<!-- {difftext.replace('-->', '--&gt;')}\n-->")
    else:
      print(difftext)
  return 1 if diffs else 0


if __name__ == "__main__":
  sys.exit(main(sys.argv))
