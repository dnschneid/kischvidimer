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

"""
Common classes and routines for handling sexp-based kicad files
"""

import math
import operator as op
import os
import re
import sys
from decimal import Decimal
from uuid import uuid4

from . import sexp
from .diff import FakeDiff, Param
from .kicad_variables import Variables

# hack for keyword testing
if __name__ == "__main__":
  sexp.handler._handlers.clear()

from .kicad_modifiers import ModifierRoot

JUST_FLIP = {"left": "right", "right": "left"}
VJUST_FLIP = {"top": "bottom", "bottom": "top"}

Length = sexp.SExp.basic("length")  # length of a pin


class HasUUID:
  UNIQUE = False  # UUID-containing things are usually not unique

  @sexp.uses("uuid")
  def uuid(self, generate=False):
    if "uuid" in self:
      return self["uuid"][0][0]
    gen = getattr(self, "__uuidcache", None)
    if gen or not generate:
      return gen
    gen = str(uuid4())
    setattr(self, "__uuidcache", gen)
    return gen

  def uuid_matches(self, other):
    """Returns True if the uuids match, False if they do not match, and None if
    at least one of the UUIDs is undefined.
    """
    if not isinstance(other, HasUUID):
      return False
    this = self.uuid()
    that = other.uuid()
    return None if this is None or that is None else this == that

  def distance(self, other, fast, diffparam):
    if not isinstance(other, sexp.SExp) or self.type != other.type:
      return None
    uuid_matches = self.uuid_matches(other)
    if uuid_matches:
      return 0
    if uuid_matches is False:  # definite UUID mismatch; don't accept changes
      return None
    if fast:
      return 1
    return super().distance(other, fast, diffparam)


def variant_context(name):
  """Returns a context tuple selecting a design variant ("" for the base)."""
  return (sexp.SExp.init([sexp.Atom("~variant"), name or ""]),)


def active_variant(context):
  """Returns the name of the design variant selected in the context.
  Returns "" for the base design.
  """
  for c in reversed(context or ()):
    if getattr(c, "type", None) == "~variant":
      return c.data[0] if c.data else ""
  return ""


class HasInstanceData(sexp.SExp):
  """Sexp with instances->project->path->field/data"""

  # Instance flags: (token, whether the token is positive logic)
  FLAGS = {
    "dnp": ("dnp", False),
    "exclude_from_sim": ("exclude_from_sim", False),
    "exclude_from_bom": ("in_bom", True),
    "exclude_from_board": ("on_board", True),
    "exclude_from_pos_files": ("in_pos_files", True),
  }

  @classmethod
  def lookup(cls, field, diffs, context, default=None):
    # Instance data is tricky. Modifications are straightfoward (the
    # modification should be visually shown), but adds and removes are usually
    # associated with the addition and removal of an associated symbol/sheet.
    # Representing the add/remove as a diff would cause unnecessary visual
    # glitches when animating all diffs. For example, adding a symbol to a
    # reused sheet, where different sheets have different units, would cause the
    # symbol to glitch between the different units on any page except the first.
    # So for now, while we don't have a way of associating the add/remove diffs,
    # treat add/remove as constant states.
    project = None
    path = None
    sheet = None
    for c in reversed(context):
      if c.type == "path":
        path = c
      elif c.type == "sheet":
        sheet = c
      elif c.type == "~project":
        project = c[0]
    data = default
    if path is not None:
      uuid = path.uuid(sheet)
      for c in context:  # priority to the descendent
        if isinstance(c, cls):
          cdata = c.instancedata(project, uuid, field, diffs)
          if cdata and not cdata.is_empty:
            data = Param(cdata, default=data)
    return Param(data)

  @staticmethod
  def instance_uuid(context):
    """Returns (project, uuid) of the page instance described by the context.
    uuid is None if the context does not describe an instance.
    """
    project = None
    path = None
    sheets = []
    for c in context or ():
      typ = getattr(c, "type", None)
      if typ == "~project":
        project = c[0]
      elif typ == "path":
        path = c
        sheets = []
      elif typ == "sheet":
        sheets.append(c)
    if path is not None:
      return project, path.uuid(sheets[:1])
    # Standalone pages use a pair of fake sheets instead of a path
    if len(sheets) >= 2:
      return project, f"{sheets[0].uuid()}/{sheets[1].uuid()}"
    return project, None

  def variant_lookup(self, getter, diffs, context):
    """Returns a Param of getter(variant, diffs) for this object's instance
    data for the active variant in the context, or Param(None) if there is
    none. Unlike other instance data, adding or removing a variant (or its
    contents) is tracked as a diff.
    """
    variant = active_variant(context)
    if not variant:
      return Param(None)
    project, uuid = HasInstanceData.instance_uuid(context)
    if uuid is None:
      return Param(None)
    data = self.instancedata(
      project,
      uuid,
      lambda path, diffs: path.variantdata(variant, getter, diffs),
      diffs,
    )
    return Param(data)

  def variant_sensitive(self, diffs, context):
    """Returns True if any instance has data for the active variant."""
    variant = active_variant(context)
    if not variant:
      return False
    cache = self.__dict__.setdefault("_variant_sensitive_cache", {})
    key = (id(diffs), variant)
    if key not in cache:
      cache[key] = any(
        v.name == variant
        for path in self.paths(None, diffs).values()
        for v in path.variants(diffs)
      )
    return cache[key]

  def flag(self, flag, diffs=None, context=None):
    """Returns a Param of an instance flag (see FLAGS) for the active variant.
    Only considers this object's own flag, not parent sheets.
    """
    token, positive = HasInstanceData.FLAGS[flag]
    if positive:
      # A missing token means the default: not excluded
      base = self.getparam(token, diffs, get=lambda c, d: c.yes(d))
      base = base.map(lambda y: y is False)
    else:
      base = self.has_yes(token, diffs)

    def getter(variant, diffs):
      value = variant.flag(token, diffs)
      # Before 20260306, a variant's in_bom token was negative logic
      if positive and not (token == "in_bom" and variant.version < 20260306):
        value = value.map(lambda y: None if y is None else not y)
      return value

    return Param(self.variant_lookup(getter, diffs, context), default=base)

  def inherited_flag(self, flag, context):
    """Returns True if the flag is set on this object or on any sheet in its
    hierarchy, for the active variant. Ignores diffs.
    """
    if self.flag(flag, None, context).v:
      return True
    _project, uuid = HasInstanceData.instance_uuid(context)
    for c in context:
      if hasattr(c, "kicad_pro") and uuid:
        return c.kicad_pro.sheet_flag(flag, uuid, context)
    return False

  def instancedata(self, project, uuid, field, diffs):
    # Disregard diffs on additions and removals
    added, _removed = self.added_and_removed(diffs, Instances)
    data = None
    # Earlier results take precedence
    for instances in self.getsubs("instances") + added:
      newdata = instances.instancedata(project, uuid, field, diffs)
      if newdata and not newdata.is_empty:
        data = Param(data, default=newdata)
    return data

  def paths(self, project=None, diffs=None):
    # Disregard diffs on additions and removals
    added, _removed = self.added_and_removed(diffs, Instances)
    paths = {}
    # Added results get overridden (preexisting ones take precedence)
    for instances in added + self.getsubs("instances"):
      paths.update(instances.paths(project, diffs))
    return paths


@sexp.handler("instances")
class Instances(sexp.SExp):
  """Tracks instances of a sheet or symbol"""

  @sexp.uses("project")
  def paths(self, project=None, diffs=None):
    """Returns a dict of instance to path elements, including added/removed."""
    # conveniently resolve ~project
    if isinstance(project, sexp.SExp):
      project = project[0]
    # Disregard diffs on additions and removals
    # Added get precedence
    added, _removed = self.added_and_removed(diffs, Project)
    all_projects = [a.v for a in added] + self.getsubs("project")
    paths = {}
    # Named projects get precedence
    for prj in all_projects:
      project = project or prj.name
      for path in prj.paths(project, diffs):
        paths.setdefault(path.path, path)
    # Check for buggy unnamed projects
    for prj in all_projects:
      for path in prj.paths(project, diffs):
        paths.setdefault(path.path, path)
    return paths

  def instancedata(self, project, uuid, field, diffs):
    # Disregard diffs on additions and removals
    added, _removed = self.added_and_removed(diffs, Project)
    data = None
    # Prioritize added data, mainly for project matching if project is unknown
    all_projects = [a.v for a in added] + self.getsubs("project")
    # Named projects get precedence
    for prj in all_projects:
      project = project or prj.name
      newdata = prj.instancedata(project, uuid, field, diffs)
      if newdata and not newdata.is_empty:
        data = Param(data, default=newdata)
    # Check for buggy unnamed projects
    for prj in all_projects:
      project = project or prj.name
      newdata = prj.instancedata("", uuid, field, diffs)
      if newdata and not newdata.is_empty:
        data = Param(data, default=newdata)
    # KiCad matches instances by path alone, so fall back to other projects
    if data is None:
      for prj in all_projects:
        newdata = prj.instancedata(prj.name, uuid, field, diffs)
        if newdata and not newdata.is_empty:
          return newdata
    return data


@sexp.handler("project")
class Project(sexp.SExp):
  UNIQUE = "name"
  LITERAL_MAP = {"name": 1}

  @property
  def name(self):
    return self[0]

  def paths(self, project, diffs):
    """Returns all known paths, whether added/modified/removed."""
    if project is not None and project != self.name:
      return []
    # Disregard diffs on additions and removals
    added, _removed = self.added_and_removed(diffs, Path)
    return [a.v for a in added] + self.getsubs("path")

  def instancedata(self, project, uuid, field, diffs):
    data = None
    # Order shouldn't matter since there should only be one match
    for path in self.paths(project, diffs):
      newdata = path.instancedata(uuid, field, diffs)
      if newdata and not newdata.is_empty:
        data = Param(data, default=newdata)
    return data


@sexp.handler("path")
class Path(sexp.SExp):
  UNIQUE = "path"
  LITERAL_MAP = {"path": 1}

  @property
  def path(self):
    return self[0]

  def uuid(self, ref=None, generate=False):
    if ref and not isinstance(ref, (tuple, list)):
      ref = [ref]
    if not ref:
      return self.path
    return f"{self[0]}/{'/'.join(r.uuid(generate=generate) for r in ref)}"

  def instancedata(self, uuid, field, diffs):
    if uuid != self.path:
      return None
    if callable(field):
      return field(self, diffs)
    # Disregard diffs on additions and removals
    added, _removed = self.added_and_removed(diffs, sexp.SExp.get_class(field))
    data = None
    # Order shouldn't matter since there should only be one match
    for d in [a.v for a in added] + (self[field] if field in self else []):
      newdata = d.param(diffs)
      if newdata and not newdata.is_empty:
        data = Param(data, default=newdata)
    return data

  def variants(self, diffs=None):
    """Returns all variant blocks, including ones added by diffs."""
    added, _removed = self.added_and_removed(diffs, Variant)
    return self.getsubs("variant") + [a.v for a in added]

  def variantdata(self, variant, getter, diffs):
    """Returns a Param of getter(variant block, diffs), tracking diffs that add
    or remove the variant block."""
    return self.getparam(
      "variant", diffs, match=lambda v: v.name == variant, get=getter
    )


def diff_variant(diff):
  """Returns the name of the design variant a diff changes, or None if the diff
  isn't specific to a variant."""
  if diff.is_instance(Variant):
    return next(v.name for v in diff.values if isinstance(v, Variant))
  target = diff.target
  for item in (target, *getattr(target, "ancestry", ())):
    if isinstance(item, Variant):
      return item.name
  return None


@sexp.handler("variant")
class Variant(sexp.SExp):
  """Design variant overrides of a symbol or sheet instance.
  (variant (name "X") (dnp yes) (in_bom no) (field (name "F") (value "V")) ...)
  """

  UNIQUE = False
  LITERAL_MAP = {}

  def __str__(self):
    return f"{self.type} '{self.name}'"

  @property
  @sexp.uses("name")
  def name(self):
    return self["name"][0][0] if "name" in self else ""

  @property
  def version(self):
    """File version of the schematic this variant block is in."""
    for a in self.ancestry:
      if a.type == "kicad_sch" and "version" in a:
        return a["version"][0][0]
    return Version.MAX_VERSION

  def distance(self, other, fast, diffparam):
    if not isinstance(other, sexp.SExp) or self.type != other.type:
      return None
    # Renaming a variant is treated as a remove + add
    return 0 if self.name == other.name else None

  def flag(self, token, diffs=None):
    """Returns a Param of True/False if the token is overridden, else None."""
    return self.getparam(token, diffs, get=lambda c, d: c.yes(d))

  def field(self, name, diffs=None):
    """Returns a Param of the field override's text, or None."""
    return self.getparam(
      "field",
      diffs,
      match=lambda f: f.name == name,
      get=lambda f, d: f.getparam("value", d),
    )

  def fields(self):
    """Returns a dict of field name to override text. Ignores diffs."""
    return {f.name: f.getparam("value").v for f in self.getsubs("field")}

  @sexp.uses("symbol_override")
  def symbol_override(self, diffs=None):
    """Returns a Param of the alternate library symbol's ID, or None."""
    return self.getparam("symbol_override", diffs)


@sexp.handler("field")
class VariantField(sexp.SExp):
  """Field override within a variant: (field (name "F") (value "V"))"""

  UNIQUE = False
  LITERAL_MAP = {}

  def __str__(self):
    return f"{self.type} '{self.name}'"

  @property
  def name(self):
    return self["name"][0][0] if "name" in self else ""

  def distance(self, other, fast, diffparam):
    if not isinstance(other, sexp.SExp) or self.type != other.type:
      return None
    return 0 if self.name == other.name else None


Value = sexp.SExp.basic("value")  # variant field value, legacy instance value
SymbolOverride = sexp.SExp.basic("symbol_override")  # variant alternate symbol


@sexp.handler("version")
class Version(sexp.SExp):
  """File version"""

  MIN_VERSION = 20220000  # kicad 6.99
  MAX_VERSION = 20261015  # kicad 10.99 (master)

  def __init__(self, s):
    super().__init__(s)

  @property
  def is_supported(self):
    return self.MIN_VERSION <= self.data[0] <= self.MAX_VERSION

  @staticmethod
  def check(root, cls, what, fname=None):
    """Raises UnsupportedFileError unless root is a cls (described by what)
    with a supported file version."""
    if not isinstance(root, cls):
      raise UnsupportedFileError(fname, f"not a {what}")
    if "version" not in root:
      raise UnsupportedFileError(fname, "missing KiCad file version")
    version = root["version"][0]
    if not version.is_supported:
      raise UnsupportedFileError(
        fname,
        f"unsupported KiCad file version {version.data[0]} "
        f"(supported: {Version.MIN_VERSION}-{Version.MAX_VERSION})",
      )


class UnsupportedFileError(Exception):
  """Raised when loading a file kischvidimer cannot handle"""

  def __init__(self, fname, reason):
    super().__init__(f"{fname or '<stdin>'}: {reason}")
    self.reason = reason


@sexp.handler("data")
class Data(sexp.SExp):
  """Raw data."""

  LITERAL_MAP = {"data": (1, -1)}

  def b64(self, diffs):
    return self.param(diffs).map("".join)


@sexp.handler("at")
class Coord(sexp.SExp):
  """A set of offset or coordinates, and sometimes rotation"""

  LITERAL_MAP = {"pos": (1, 2), "rot": 3}

  def pos(self, diffs=None, relative=False):
    # FIXME: diffs
    relativeto = not relative and self._find_ancestor_pos(diffs)
    assert not (relativeto and diffs), "can't do absolute locations with diffs"
    relativeto = relativeto or (0, 0)
    return self.param(diffs, "pos", self._relpos[:2]).map(
      Coord.add_pos, relativeto
    )

  def raw_pos(self, diffs=None):
    """Useful for cases where an absolute location bubbles up to the parent."""
    return self.param(diffs, "pos", self.data[:2])

  def rot(self, diffs=None, context=None):
    # see SCH_IO_KICAD_SEXPR_PARSER::parseText()
    return Param(
      lambda r: r or 0 if (r or 0) < 360 else r / 10,
      self.param(diffs, "rot"),
    )

  def reparent(self, new_parent):
    super().reparent(new_parent)
    relativeto = self._find_ancestor_pos()
    relativeto = relativeto[0].v if relativeto else (0, 0)
    self._relpos = (self._sexp[1] - relativeto[0],)
    if len(self._sexp) >= 3:
      self._relpos += (self._sexp[2] - relativeto[1],)

  def _find_ancestor_pos(self, diffs=None):
    # The first thing with "at" in the parentage is a good pick.
    for parent in self.ancestry:
      # Table elements have their position as the first cell's.
      if parent.type == "cells":
        return parent.pos()
      # TODO: if we do this, rendering needs to be updated
      # # Arcs, rects, etc are relative to their start pos
      # if self.type in ("start", "mid", "end"):
      #   return parent.get("start").get("start").data[:2]
      # # Polylines, bezier, etc, are relative to their first coordinate
      # if self.type == "xy":
      #   return parent["xy"][0].data[:2]
      at = parent.get("at")
      if at is not None and at is not self:
        return at.pos()
    return None

  @property
  def sexp(self):
    """sexp for the purposes of outputting to a file."""
    pos = self.pos()
    self._sexp[1] = pos.v[0]
    if len(self._sexp) >= 3:
      self._sexp[2] = pos.v[1]
    return self._sexp

  @property
  def comp_sexp(self):
    """sexp for the purposes of comparison."""
    ret = self._sexp.copy()
    ret[1:3] = self._relpos
    return ret

  def sortkey(self):
    """Sort key that tries to be more stable between changes."""
    # Add the two coordinates together in an attempt to get a sort key that's
    # less affected by small moves. Appends remaining data for uniqueness.
    metric = sum(self.data[:2])
    return " ".join(f"{x:08d}" for x in (metric,) + self.data[1:])

  def distance(self, other, fast, _diffparam):
    """Returns the distance between two points."""
    if not isinstance(other, sexp.SExp) or self.type != other.type:
      return None
    if fast:
      return self != other
    return math.hypot(
      *(x - y for x, y in zip(self._relpos, other._relpos, strict=True))
    ) + (len(self.data) != len(other.data) or self.data[2:] != other.data[2:])

  def __eq__(self, other):
    if not isinstance(other, sexp.SExp) or self.type != other.type:
      return False
    lendata = len(self.data)
    if lendata != len(other.data):
      return False
    if lendata > 2 and self.data[2:] != other.data[2:]:
      return False
    return self._relpos == other._relpos

  @staticmethod
  def add_pos(a, b):
    if len(a) == 1:
      return (a[0] + b[0], 0)
    return (a[0] + b[0], a[1] + b[1])


@sexp.handler("pos", "center", "start", "mid", "end")
class GravCoord(Coord):
  """Coordinate without rotation, possibly with gravity."""

  LITERAL_MAP = {"pos": (1, 2), "gravity": 3}

  def gravity(self, diffs=None):
    return self.param(diffs, "gravity", default="rbcorner")

  def rot(self, diffs=None, context=None):
    raise Exception("rot() not valid on GravCoord")


@sexp.handler("xy")
class MultiCoord(Coord):
  """Coordinate that might be one of many."""

  UNIQUE = False
  ORDERED = True
  LITERAL_MAP = {"pos": (1, 2)}

  def distance(self, other, fast, diffparam):
    if not isinstance(other, sexp.SExp) or self.type != other.type:
      return None
    this_i = None
    other_i = None
    for i, xy in enumerate(self.parent["xy"]):
      if self is xy:
        this_i = i
        break
    for i, xy in enumerate(other.parent["xy"]):
      if other is xy:
        other_i = i
        break
    return abs(this_i - other_i)


class Drawable(ModifierRoot, sexp.SExp):
  UNIQUE = False

  DRAW_WKS = 1 << 0  # worksheet
  DRAW_WKS_PG = 1 << 1  # page-specific worksheet elements
  DRAW_SYMBG = 1 << 2
  DRAW_BG = 1 << 3
  DRAW_IMG = 1 << 4
  DRAW_PINS = 1 << 5
  DRAW_SYMFG = DRAW_PINS  # context: schematic, includes pins/fg/text
  DRAW_TEXT_PG = 1 << 6  # page-specific text (variables)
  DRAW_PROPS_PG = 1 << 7  # page-specific props (variables, refdes)
  DRAW_FG_PG = 1 << 8  # page-specific foreground elements
  DRAW_TEXT = 1 << 9
  DRAW_PROPS = 1 << 10
  DRAW_FG = 1 << 11
  DRAW_MODES = 12
  DRAW_ALL = (1 << DRAW_MODES) - 1
  DRAW_SEQUENCE = tuple(1 << i for i in range(DRAW_MODES))
  DRAW_STAGE_COMMON_BG = DRAW_WKS | DRAW_IMG | DRAW_BG
  DRAW_STAGE_PAGE_SPECIFIC = (
    DRAW_WKS_PG
    | DRAW_SYMBG
    | DRAW_SYMFG
    | DRAW_TEXT_PG
    | DRAW_PROPS_PG
    | DRAW_FG_PG
  )
  DRAW_STAGE_COMMON_FG = DRAW_TEXT | DRAW_PROPS | DRAW_FG

  def __str__(self):
    at = self.get("at")
    if at is None:
      raise NotImplementedError(
        f"__str__ not defined for {self.__class__.__name__}"
      )
    pos = at.pos().v
    return f"{self.type} at ({pos[0]}, {pos[1]})"

  def __init__(self, data=None):
    super().__init__(data)
    # Drop the private flag so that literals line up; it stays in _atoms
    flag = self._sexp[1] if len(self._sexp) > 1 else None
    if isinstance(flag, sexp.Atom) and flag == "private":
      del self._sexp[1]

  @property
  def editor_only(self):
    """Private shapes and text are symbol editor notes, which KiCad doesn't
    draw in schematics."""
    return sexp.Atom("private") in self._atoms

  def fillsvg(self, svg, diffs, draw=DRAW_ALL, context=None):
    if not isinstance(context, tuple):
      context = () if context is None else (context,)
    context = context + (self,)
    # Can't query netlister for new objects, since they aren't in it.
    # Check the type name to avoid circular imports.
    new_context = tuple(c for c in context if not hasattr(c, "netlister"))
    added, removed = self.added_and_removed(diffs, Drawable)
    for subdraw in Drawable.DRAW_SEQUENCE:
      if draw & subdraw:
        # new stuff!
        for item, c in added:
          if item.editor_only:
            continue
          svg.gstart(hidden=FakeDiff(c, old=True, new=False).param())
          item.fillsvg(svg, diffs, subdraw, new_context)
          svg.gend()
        # existing stuff, maybe removed
        for item in self.data:
          if isinstance(item, Drawable) and not item.editor_only:
            rm_c = removed.get(id(item))
            if rm_c:
              svg.gstart(hidden=FakeDiff(rm_c, old=False, new=True).param())
            item.fillsvg(svg, diffs, subdraw, context)
            if rm_c:
              svg.gend()

  def fillvars(self, variables, diffs, context=None):
    # FIXME: should we punt on diffs?
    if not isinstance(context, tuple):
      context = () if context is None else (context,)
    context = context + (self,)
    for item in self.data:
      if isinstance(item, Drawable):
        item.fillvars(variables, diffs, context)

  def fillnetlist(self, netlister, diffs, context=None):
    # FIXME: should we punt on diffs?
    if not isinstance(context, tuple):
      context = () if context is None else (context,)
    context = context + (self,)
    for item in self.data:
      if isinstance(item, Drawable):
        item.fillnetlist(netlister, diffs, context)

  @staticmethod
  def draw_as_pg(draw):
    """Remaps draw flags so that an element is entirely drawn during the
    page-specific stages, e.g. if it differs between instances.
    Returns 0 if nothing should be drawn during this stage.
    """
    if not draw & Drawable.DRAW_STAGE_PAGE_SPECIFIC:
      return 0
    for pg, common in (
      (Drawable.DRAW_SYMBG, Drawable.DRAW_BG),
      (Drawable.DRAW_TEXT_PG, Drawable.DRAW_TEXT),
      (Drawable.DRAW_PROPS_PG, Drawable.DRAW_PROPS),
      (Drawable.DRAW_FG_PG, Drawable.DRAW_FG),
    ):
      if draw & pg:
        draw |= common
    return draw

  @staticmethod
  def draw_body(draw, fill):
    if isinstance(fill, dict):
      fill = fill["fill"]
    body_draw = (
      Drawable.DRAW_FG
      if Param(fill).reduce(any, lambda f: f == "outline")
      else Drawable.DRAW_BG
    )
    return draw & body_draw != 0

  def fillsvgargs(self, args, diffs, context=None):
    layer_color = args.get("color")
    super().fillsvgargs(args, diffs, context)
    hatch = args.pop("hatch", None)
    if hatch is None:
      return
    # An unset hatch color is the layer color, not the stroke color. The
    # hatch masks the fill and is sized by the stroke width.
    args["fill"] = Param(
      lambda f: layer_color if f == "hatch" else f, args["fill"]
    )
    args["hatch"] = Param(
      lambda h, t: (h, t) if h else None, hatch, args.get("thick")
    )


@sexp.handler("polyline")
class Polyline(Drawable):
  """Graphical polyline"""

  def __str__(self):
    start = self["pts"][0]["xy"][0].pos().v
    return f"{self.type} from ({start[0]}, {start[1]})"

  @sexp.uses("pts")
  def pts(self, diffs=None):
    # FIXME: diffs
    return Param.array(
      *(xy.pos(diffs, relative=False) for xy in self["pts"][0]["xy"])
    )

  def fillsvg(self, svg, diffs, draw, context, tag=None):
    if not draw & (Drawable.DRAW_BG | Drawable.DRAW_FG):
      return
    # Don't try to render background only if there are only two points?
    # FIXME: diffs?
    xys = self.pts(diffs)
    if not draw & Drawable.DRAW_FG and xys.reduce(max, len) <= 2:
      return
    close = False
    default_color = "notes"
    default_thick = "wire"
    if self.type == "wire":
      default_color = "wire"
    if self.type == "bus":
      default_color = "bus"
      default_thick = "bus"
    elif context[-1].type == "symbol":
      default_color = "device"
    elif context[-1].type == "rule_area":
      default_color = "rule_areas"
      close = True  # Rule areas are always closed polygons
    args = {
      "xys": xys,
      "color": default_color,
      "thick": default_thick,
      "fill": "none",
    }
    if tag is not None:
      args["tag"] = tag
    if close:
      args["close"] = close
    self.fillsvgargs(args, diffs, context)
    if not draw & Drawable.DRAW_FG:
      # Line endings are only drawn with the stroke, and as in KiCad, fills
      # stop at the base of closed line endings.
      args["shorten"] = svg.ending_depths(
        args.pop("start_marker", None),
        args.pop("end_marker", None),
        args["thick"],
      )
      args["thick"] = 0
    if not self.draw_body(draw, args):
      args["fill"] = "none"
    self._draw(svg, args)

  @staticmethod
  def _draw(svg, args):
    svg.polyline(**args)


@sexp.handler("bezier")
class Bezier(Polyline):
  """Graphical bezier curve. Format is basically the same as a polyline."""

  @staticmethod
  def _draw(svg, args):
    svg.bezier(**args)


@sexp.handler("arc")
class Arc(Drawable):
  """Graphical arc"""

  def __str__(self):
    start = self["start"][0].pos().v
    return f"{self.type} from ({start[0]}, {start[1]})"

  def fillsvg(self, svg, diffs, draw, context):
    if not draw & (Drawable.DRAW_BG | Drawable.DRAW_FG):
      return
    args = {
      "start": self["start"][0].pos(diffs, relative=True),
      "mid": self["mid"][0].pos(diffs, relative=True),
      "stop": self["end"][0].pos(diffs, relative=True),
      "color": "device" if context[-1].type == "symbol" else "notes",
      "thick": "wire",
    }
    args["fill"] = f"{args['color']}_background"
    self.fillsvgargs(args, diffs, context)
    filled = Param(args["fill"]).reduce(any, lambda f: f and f != "none")
    if filled and self.draw_body(draw, args):
      # Line endings are only drawn with the stroke, and as in KiCad, fills
      # stop at the base of closed line endings.
      bgargs = {k: v for k, v in args.items() if not k.endswith("_marker")}
      bgargs["shorten"] = svg.ending_depths(
        args.get("start_marker"), args.get("end_marker"), args["thick"]
      )
      bgargs["thick"] = 0
      svg.arc(**bgargs)
    if draw & Drawable.DRAW_FG:
      args["fill"] = "none"
      svg.arc(**args)


@sexp.handler("circle")
class Circle(Drawable):
  """Graphical circle"""

  def __str__(self):
    center = self["center"][0].pos().v
    radius = self["radius"][0][0]
    descr = self.type
    if radius > 100:
      descr = f"large {descr}"
    return f"{descr} at ({center[0]}, {center[1]})"

  @sexp.uses("radius")
  def fillsvg(self, svg, diffs, draw, context):
    if not draw & (Drawable.DRAW_BG | Drawable.DRAW_FG):
      return
    args = {
      "pos": self["center"][0].pos(diffs, relative=True),
      "radius": self["radius"][0].param(diffs),
      "color": "device" if context[-1].type == "symbol" else "notes",
    }
    args["fill"] = f"{args['color']}_background"
    self.fillsvgargs(args, diffs, context)
    if not draw & Drawable.DRAW_FG:
      args["thick"] = 0
    if not self.draw_body(draw, args):
      args["fill"] = "none"
    svg.circle(**args)


MajorRadius = sexp.SExp.basic("major_radius")  # ellipse rx
MinorRadius = sexp.SExp.basic("minor_radius")  # ellipse ry
RotationAngle = sexp.SExp.basic("rotation_angle")  # ellipse rotation
StartAngle = sexp.SExp.basic("start_angle")  # ellipse_arc start
EndAngle = sexp.SExp.basic("end_angle")  # ellipse_arc end


@sexp.handler("ellipse")
class Ellipse(Drawable):
  """Graphical ellipse.
  Angles are in degrees in KiCad's internal y-down space, so are clockwise
  on-screen, even in symbol libraries where the center's y is inverted.
  """

  def __str__(self):
    center = self["center"][0].pos().v
    radius = max(self["major_radius"][0][0], self["minor_radius"][0][0])
    descr = self.type.replace("_", " ")
    if radius > 100:
      descr = f"large {descr}"
    return f"{descr} at ({center[0]}, {center[1]})"

  def ellipseargs(self, diffs):
    # Defaults match SCH_IO_KICAD_SEXPR_PARSER::parseEllipseBody
    return {
      "pos": self["center"][0].pos(diffs, relative=True),
      "rx": self.getparam("major_radius", diffs, 1),
      "ry": self.getparam("minor_radius", diffs, 1),
      "rotate": self.getparam("rotation_angle", diffs, 0),
    }

  def fillsvg(self, svg, diffs, draw, context):
    if not draw & (Drawable.DRAW_BG | Drawable.DRAW_FG):
      return
    args = self.ellipseargs(diffs)
    args["color"] = "device" if context[-1].type == "symbol" else "notes"
    args["fill"] = f"{args['color']}_background"
    self.fillsvgargs(args, diffs, context)
    if not draw & Drawable.DRAW_FG:
      args["thick"] = 0
      # Line endings are only drawn with the stroke
      args.pop("start_marker", None)
      args.pop("end_marker", None)
    if not self.draw_body(draw, args):
      args["fill"] = "none"
    svg.ellipse(**args)


@sexp.handler("ellipse_arc")
class EllipseArc(Ellipse):
  """Graphical elliptical arc. Fills render as a chord, as when plotted."""

  def ellipseargs(self, diffs):
    args = super().ellipseargs(diffs)
    args["start"] = self.getparam("start_angle", diffs, 0)
    args["end"] = self.getparam("end_angle", diffs, 90)
    return args


@sexp.handler("rectangle")
class Rectangle(Drawable):
  """Graphical rectangle"""

  def __str__(self):
    start = self["start"][0].pos().v
    end = self["end"][0].pos().v
    descr = self.type
    if (start[0] - end[0]) ** 2 + (start[1] - end[1]) ** 2 > 100**2:
      descr = f"large {descr}"
    return f"{descr} at ({start[0]}, {start[1]})"

  def fillsvg(self, svg, diffs, draw, context):
    if not draw & (Drawable.DRAW_BG | Drawable.DRAW_FG):
      return
    args = {
      "pos": self["start"][0].pos(diffs, relative=True),
      "end": self["end"][0].pos(diffs, relative=True),
      "color": "device" if context[-1].type == "symbol" else "notes",
    }
    args["fill"] = f"{args['color']}_background"
    self.fillsvgargs(args, diffs, context)
    if not draw & Drawable.DRAW_FG:
      args["thick"] = 0
    if not self.draw_body(draw, args):
      args["fill"] = "none"
    svg.rect(**args)


@sexp.handler("text")
class Text(Drawable):
  """Graphical text"""

  LITERAL_MAP = {"text": 1}

  def fillsvg(self, svg, diffs, draw, context):
    # FIXME: diffs
    subcontext = context + (self,)
    variables = Variables.v(context)
    raw_text = self.param(diffs)
    text = Param(
      lambda t: variables.expand(subcontext, Variables.unescape(t)), raw_text
    )
    is_pg = raw_text.reduce(any, lambda t: "${" in t)
    if not draw & (Drawable.DRAW_TEXT_PG if is_pg else Drawable.DRAW_TEXT):
      return
    args = {
      "text": text,
      "pos": self["at"][0].pos(diffs, relative=True),
      "rotate": self["at"][0].rot(diffs),
      "textcolor": "device" if context[-1].type == "symbol" else "notes",
    }
    self.fillsvgargs(args, diffs, context)
    svg.text(**args)


@sexp.handler("text_box")
class TextBox(Drawable):
  """Graphical text, but in a box!"""

  LITERAL_MAP = {"text": 1}

  def __str__(self):
    pos, size = self.pos_size(None)
    descr = self.type.replace("_", " ")
    if size.v[0] ** 2 + size.v[1] ** 2 > 100**2:
      descr = f"large {descr}"
    return f"{descr} at ({pos.v[0]}, {pos.v[1]})"

  def expand_text(self, variables, context, text, depth=0):
    return variables.expand(context, Variables.unescape(text))

  @sexp.uses("pos", "size")
  def pos_size(self, diffs, relative=False, raw_pos=False):
    if not raw_pos:
      pos = self["at"][0].pos(diffs, relative=relative)
    else:
      pos = self["at"][0].raw_pos(diffs)
    size = self["size"][0].param(diffs)
    return Param.multi(
      2,
      lambda p, s: (
        (p[0] + s[0] if s[0] < 0 else p[0], p[1] + s[1] if s[1] < 0 else p[1]),
        (abs(s[0]), abs(s[1])),
      ),
      pos,
      size,
    )

  def fillsvg(self, svg, diffs, draw, context):
    # FIXME: rotation/flipping, both in and out of symbols
    subcontext = context + (self,)
    variables = Variables.v(context)
    raw_text = self.param(diffs)
    is_pg = raw_text.reduce(any, lambda t: "${" in t)
    is_symbol = any(c.type == "symbol" for c in context)
    args = {
      "color": "device" if is_symbol else "notes",
      "textcolor": "device" if is_symbol else "notes",
      "fill": "none",
      "thick": "wire",
    }
    self.fillsvgargs(args, diffs, context)
    margins = Param(
      lambda m, s: m if m else [Decimal(s * 4) / 5] * 4,
      args.pop("margins", None),
      args.get("textsize", 0),
    )
    pos, size = self.pos_size(diffs, relative=True)
    if (
      draw & Drawable.DRAW_BG
      or draw & Drawable.DRAW_FG
      and self.type != "table_cell"
    ):
      rargs = {
        x: args[x]
        for x in ("color", "fill", "thick", "pattern", "hatch")
        if x in args
      }
      rargs["pos"] = pos
      rargs["width"], rargs["height"] = Param.multi(2, size)
      # stroke of <0 means no border. stroke of 0 means default
      rargs["thick"] = Param(
        lambda t: t if t is None or isinstance(t, str) or t >= 0 else 0,
        rargs.get("thick"),
      )
      if not draw & Drawable.DRAW_FG:
        rargs["thick"] = 0
      if not self.draw_body(draw, args):
        rargs["fill"] = "none"
      svg.rect(**rargs)
    if draw & (Drawable.DRAW_TEXT_PG if is_pg else Drawable.DRAW_TEXT):
      # halve the right margin to account for character spacing
      wrapwidth = Param(lambda s, m: s[0] - m[0] - m[2] / 2, size, margins)
      unwrapped = Param(
        lambda t: self.expand_text(variables, subcontext, t), raw_text
      )
      textwidth = Param(
        op.mul, args.get("textsize", 0), args.get("textscale", 1)
      )
      args["text"] = Param(
        TextBox.wrap_text, svg, unwrapped, textwidth, wrapwidth
      )
      # symbols have Y inverted, so compensate by swapping the vjust calcs.
      # svg will handle the rest
      vjust = ("bottom", "top") if is_symbol else ("top", "bottom")
      args["pos"] = Param(
        lambda p, s, m, j, vj: (
          float(p[0])
          + float(
            m[0] if j == "left" else s[0] - m[2] if j == "right" else s[0] / 2
          ),
          p[1]
          + (
            m[1]
            if vj == vjust[0]
            else s[1] - m[3]
            if vj == vjust[1]
            else s[1] / 2
          ),
        ),
        *(pos, size, margins, args.get("justify"), args.get("vjustify")),
      )
      for unneeded in "color", "fill", "thick", "size", "pattern", "hatch":
        args.pop(unneeded, None)
      svg.text(**args)

  @staticmethod
  def wrap_text(svg, text, size, wrapwidth):
    """Adds newlines to text based on the size and width available."""
    lines = []
    # wrap rules: only wrap on space and don't split words.
    # sequential spaces can cause additional wraps.
    # wrapped lines are trimmed to the first non-space.
    for src in text.split("\n"):
      trim = False
      words = src.split(" ")
      line = words[0]
      # TODO: obvious optimization opportunities here; profile to see if needed
      for word in words[1:]:
        if svg.calcwidth(f"{line} {word}", size) > wrapwidth:
          lines.append(line)
          line = word
          trim = True
        elif word and trim:
          line = f"{line} {word}".lstrip(" ")
        else:
          line = f"{line} {word}"
      lines.append(line)
    return "\n".join(lines)


@sexp.handler("table_cell")
class TableCell(TextBox):
  ORDERED = True

  RE_CELL_VAR = re.compile(r"\$\{(ROW|COL|ADDR|CELL\(([^$]*?)\))\}")
  RE_ADDR = re.compile(r"([A-Za-z]+)([0-9]+)")

  def row_col(self):
    pos = self["at"][0].pos(relative=True).v
    return self.parent.to_row_col(pos, relative=True)

  def expand_text(self, variables, context, text, depth=0):
    """Expands the cell variables ${ROW}, ${COL}, ${ADDR} and ${CELL(...)}
    before the rest, like KiCad's SCH_TABLECELL::GetShownText"""
    row, col = self.row_col()

    def sub(m):
      if m[1] in ("ROW", "COL"):
        return str(row if m[1] == "ROW" else col)
      if m[1] == "ADDR":
        return f"{chr(ord('A') + col % 26)}{row}"
      return self.cell_text(variables, context, m[2].strip(), depth)

    # Twice, for e.g. ${CELL(${ROW},0)}
    text = TableCell.RE_CELL_VAR.sub(sub, TableCell.RE_CELL_VAR.sub(sub, text))
    return super().expand_text(variables, context, text)

  def cell_text(self, variables, context, args, depth):
    """Returns the text of the cell ${CELL(args)} refers to"""
    addr = TableCell.RE_ADDR.fullmatch(args[1:-1])
    if args[:1] == args[-1:] == '"' and addr:
      row, col = int(addr[2]), 0
      for c in addr[1].upper():
        col = col * 26 + ord(c) - ord("A") + 1
      col -= 1
    elif args[:1] == args[-1:] == '"':
      return f"<Unresolved: Invalid cell address: {args[1:-1]}>"
    elif "," in args:
      row, _, col = args.partition(",")
      try:
        row, col = int(row), int(col)
      except ValueError:
        return f"<Unresolved: Invalid cell coordinates: {args}>"
    else:
      return f"<Unresolved: Invalid CELL() syntax: {args}>"
    for cell in self.parent["table_cell"]:
      if cell.row_col() == (row, col):
        if depth >= Variables.MAX_DEPTH:
          return "<Circular reference>"
        text = cell.param(None).v
        return cell.expand_text(variables, context, text, depth + 1)
    addr = f"{chr(ord('A') + col % 26)}{row}" if row >= 0 <= col else args
    return f"<Unresolved: Cell {addr} not found>"

  def __str__(self):
    pos = self["at"][0].pos(relative=True).v
    rc = self.parent.to_row_col(pos, relative=True)
    return f"cell {unit_to_alpha(rc[1] + 1)}{rc[0] + 1}"

  def distance(self, other, fast, diffparam):
    if not isinstance(other, sexp.SExp) or self.type != other.type:
      return None
    # Consider cells the same if they are the same row+column
    thispos = self["at"][0].pos(relative=True).v
    thatpos = other["at"][0].pos(relative=True).v
    thisrc = self.parent.to_row_col(thispos, relative=True)
    thatrc = other.parent.to_row_col(thatpos, relative=True)
    return abs(thisrc[0] - thatrc[0]) + abs(thisrc[1] - thatrc[1])


@sexp.handler("property")
class Field(Drawable):
  """Properties/fields in labels, sheets, and symbols"""

  UNIQUE = "name"
  LITERAL_MAP = {"name": 1, "value": 2}

  # Unlike private shapes and text, KiCad still draws private fields
  editor_only = False

  def __str__(self):
    return f"{self.type} '{self.name}'"

  @property
  def name(self):
    return self[0]

  @property
  def value(self):
    return self[1]

  def distance(self, other, fast, diffparam):
    if not isinstance(other, sexp.SExp) or self.type != other.type:
      return None
    # Changing field names is not supported, since many fields are special.
    return 0 if self.name == other.name else None

  # Fields that design variants can't override
  VARIANT_IMMUTABLE = ("Reference", "Sheetname", "Sheetfile")

  def variant_override(self, diffs, context):
    """Returns a Param of the active variant's override of this field's text
    for the instance in context (which should end with the field's parent), or
    Param(None) if there isn't one.
    """
    parent = context[-1] if context else None
    if not isinstance(parent, HasInstanceData) or (
      self.name in Field.VARIANT_IMMUTABLE
    ):
      return Param(None)
    return parent.variant_lookup(
      lambda v, d: v.field(self.name, d), diffs, context
    )

  def fillvars(self, variables, diffs, context):
    value = self.variant_override(None, context).v
    if self.name == "Reference" and hasattr(context[-1], "reference"):
      value = context[-1].reference(None, context[:-1]).v
    variables.define(
      context + (self,), self.name, self.value if value is None else value
    )
    super().fillvars(variables, diffs, context)

  @sexp.uses("show_name", "hide")
  def fillsvg(self, svg, diffs, draw, context):
    # FIXME: diffs...
    prop = self.name  # changing field names is not supported
    raw_text = Param(
      self.variant_override(diffs, context),
      default=self.param(diffs, "value"),
    )
    is_pg = raw_text.reduce(any, lambda t: "${" in t) or prop.lower() in (
      "reference",
      "sheetname",
    )
    if not draw & (Drawable.DRAW_PROPS_PG if is_pg else Drawable.DRAW_PROPS):
      return
    variables = Variables.v(context)
    show_name = self.has_yes("show_name", diffs)
    url = None
    icon = None
    # FIXME: special props should be limited to the relevant types
    #        (sheet/sym/globallabel)
    if prop == "Reference":
      textcolor = "referencepart"
      if hasattr(context[-1], "reference"):
        raw_text = context[-1].reference(diffs, context[:-1])
    elif prop == "Value":
      textcolor = "valuepart"
      for i, c in enumerate(reversed(context)):
        if hasattr(c, "power_net"):
          icon = Param(
            lambda n: "local" if (n or "").startswith("/") else None,
            c.power_net(diffs, context[: -i - 1]),
          )
          break
    elif prop == "Intersheetrefs":
      textcolor = "intersheet_refs"
    elif prop == "Sheetname":
      textcolor = "sheetname"
      url = variables.resolve(context, "SHEETPATH")
      if url:
        url = "#" + url.rstrip("/")
    elif prop == "Sheetfile":
      textcolor = "sheetfilename"
      raw_text = Param(
        lambda t, s: t if s else f"File: {t}", raw_text, show_name
      )
    elif all(c.type != "symbol" for c in context):
      textcolor = "sheetfields"
    else:
      textcolor = "fields"
    text = Param(
      lambda t, s: (
        f"{prop}: " * s
        + variables.expand(context + (self,), Variables.unescape(t))
      ),
      raw_text,
      show_name,
    )
    pos = self["at"][0].pos(diffs, relative=True)
    # Properties of labels are rendered with offsets defined by the label type
    if hasattr(context[-1], "get_text_offset"):
      pos = Param(
        translated,
        pos,
        context[-1].get_text_offset(diffs, context, is_field=True),
      )
    if not url and text.v.startswith(("http://", "https://")):
      url = text.v.partition(" ")[0]
    args = {
      "text": text,
      "prop": prop,
      "pos": pos,
      "textcolor": textcolor,
      "url": url,
      "hidden": self.has_yes("hide"),
      "icon": icon,
    }
    self.fillsvgargs(args, diffs, context)
    rot = self["at"][0].rot(diffs)
    # Symbol rotation impacts field rotation for some reason
    for c in context:
      if c.type == "symbol" and hasattr(c, "rot_mirror"):
        inst_rot_mirror = c.rot_mirror(diffs)
        rot = Param(lambda r, rm: (r + rm[0]) % 360, rot, inst_rot_mirror)
        args["justify"] = Param(
          lambda j, r, rm: JUST_FLIP.get(j, j) if rm[1] and r % 180 else j,
          args.get("justify"),
          rot,
          inst_rot_mirror,
        )
        args["vjustify"] = Param(
          lambda j, r, rm: VJUST_FLIP.get(j, j) if rm[1] and not r % 180 else j,
          args.get("vjustify"),
          rot,
          inst_rot_mirror,
        )
        break
    args["rotate"] = rot
    svg.text(**args)

  @staticmethod
  def getprop(parent, name, diffs=None, default=None):
    if isinstance(name, Param):
      return Param(Field.getprop, parent, name, diffs, default=default)
    added, removed = parent.added_and_removed(diffs, Field)
    param = default
    for prop in parent.getsubs("property"):
      if prop.name == name:
        param = prop.param(
          diffs, "value", with_remove_c=removed.get(id(prop)), default=param
        )
    added = [dp for dp in added if dp.v.name == name]
    if added:
      param = Param.adds(added, param)
    return param


@sexp.handler("image")
class Image(Drawable):
  """An image!"""

  def fillsvg(self, svg, diffs, draw, context):
    if not draw & Drawable.DRAW_IMG:
      return
    args = {}
    self.fillsvgargs(args, diffs, context)
    svg.image(
      pos=self["at"][0].pos(diffs, relative=True),
      data=self["data"][0].b64(diffs),
      **args,
    )


def unit_to_alpha(unit):
  # FIXME: is this correct?
  alpha = ""
  while unit:
    unit -= 1
    alpha = chr(ord("A") + unit % 26) + alpha
    unit //= 26
  return alpha


def translated(pos, offset):
  if not isinstance(offset, tuple):
    offset = (offset or 0, 0)
  return (pos[0] + offset[0], pos[1] + offset[1])


def rotated(pos, deg):
  if isinstance(pos, tuple):
    x, y = pos
  else:
    x, y = pos, 0
  deg = (deg or 0) % 360
  if not deg:
    return pos
  elif deg == 90:
    return (-y, x)
  elif deg == 180:
    return (-x, -y)
  elif deg == 270:
    return (y, -x)
  x = float(x)
  y = float(y)
  rad = math.radians(deg)
  cos = math.cos(rad)
  sin = math.sin(rad)
  return (x * cos - y * sin, y * cos + x * sin)


def mirrored(pos, mirror):
  if isinstance(pos, tuple):
    x, y = pos
  else:
    x, y = pos, 0
  if mirror == "y":
    return (-x, y)
  elif mirror:
    return (x, -y)
  return (x, y)


def transformed(pos, rot=0, mirror=False, translate=None):
  if any(isinstance(x, Param) for x in (pos, rot, mirror, translate)):
    return Param(transformed, pos, rot, mirror, translate)
  return translated(mirrored(rotated(pos, rot), mirror), translate)


def transformed_pin(pinpos, rot, mirror, sym_pos):
  # Transforms pins into schematic coordinates
  # y and rot are negative since symbols have inverted y
  if any(isinstance(x, Param) for x in (pinpos, rot, mirror, sym_pos)):
    return Param(transformed_pin, pinpos, rot, mirror, sym_pos)
  return transformed((pinpos[0], -pinpos[1]), -rot, mirror, sym_pos)


def main(argv):
  # Perform keyword checks to ensure all keywords are handled
  sexp.handler._handlers.clear()
  if "wks" in argv:
    from . import kicad_wks  # noqa: F401
  elif "sym" in argv:
    from . import kicad_sym  # noqa: F401
  else:
    # includes kicad_sym and kicad_wks
    from . import kicad_sch  # noqa: F401
  ret = 0
  for kwfile in argv[1:]:
    if not os.path.isfile(kwfile):
      continue
    with open(kwfile) as f:
      kws = sorted(line.strip() for line in f if line.strip())
    print(f"{kwfile}:")
    for kw in kws:
      if kw not in sexp.handler._handlers and kw not in sexp.uses._uses:
        print(f"  {kw}")
        ret = 1
  return ret


if __name__ == "__main__":
  sys.exit(main(sys.argv))
