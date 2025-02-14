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
import math
import re
import subprocess
from xml.sax.saxutils import escape

from . import bmp, jpeg, png, themes
from .diff import Param
from .kicad_common import Drawable


class Svg:
  """Helps generate an SVG image."""

  # SVGs can't handle fractional pixels, and we don't want to specify units all
  # the time. Instead, scale up all of our units. Scaling higher increases
  # precision at the cost of file size. If we have too many trailing zeros, we
  # are no better off than just specifying units all the time.
  # A SCALE of 10000 is needed to be totally accurate (due to line thicknesses)
  # A SCALE of 100 is within 1.6% for min line thicknesses -- not really visible
  SCALE = 100  # multiplies all SVG coordinates by this before rounding

  ANCHOR = {
    "left": "start",
    "middle": "middle",
    "right": "end",
    "0": "start",
    "90": "start",
    "180": "end",
    "270": "end",
  }
  VJUST = {
    "bottom": (0, "text-after-edge"),
    "wks_bottom": (0, "auto"),
    "middle": (0.5, "central"),
    "top": (1, "hanging"),
  }
  # Scale the aspect of the SVG stipple pattern by changing the constant
  PATTERN_SCALE = 1
  PATTERNS = {
    "default": (),
    "solid": (),
    "dash": (11, 4),
    "dot": (0.2, 4),
    "dash_dot": (11, 4, 0.2, 4),
    "dash_dot_dot": (11, 4, 0.2, 4, 0.2, 4),
  }
  # SVG thicknesses
  THICKNESS = {
    "ui": 0.0762,  # mm
    "wire": 0.1524,  # mm
    "bus": 0.3048,  # mm
  }
  # It seems a font of size 1.5775 as an em height of 2.54 in KiCad
  FONT_SIZE = 2.54 / 1.5775  # mm, converting KiCad glyph width to em height
  FONT_FAMILY = "kicad"
  # Scale factor trying to bring the font's width closer to the width in KiCad
  FONT_CONDENSE = 1  # condensed versions may not exist, so don't rely on it
  FONT_SPACING = 0  # in ex
  # Image scale value, above which to treat the image as pixel art
  IMAGE_PIXEL_SCALE_THRESHOLD = 10
  # Transformation types
  TRANSFORM_TYPES = {"translate", "rotate", "scale"}
  # Color types
  COLOR_TYPES = {"fill", "stroke"}
  # Pin name and pin number placeholders for text(prop)
  PROP_PIN_NAME = 1
  PROP_PIN_NUMBER = 2
  PROP_LABEL = 3
  # Properties of text that are indexed outside of generic text
  GENERIC_IGNORE = {PROP_LABEL}
  # Text to render and horiz/vert margin if there is no content
  _PLACEHOLDER = ("Empty file", 5, 10)

  def __init__(
    self,
    bgcolor="SCHEMATIC_BACKGROUND",
    header=True,
    auto_animate=(2, 1),
    mirror_text=False,
    theme=None,
  ):
    """Preps an empty SVG.
    header -- include the xml/DOCTYPE header in the output
              Should be False if this SVG is to be nested
    """
    self.data = []
    self.libraries = None
    self.image_dirs = []
    self.symbols = {}
    self.header = header
    self.vars = {}
    self.worksheet = None
    self.datadir = None
    self.uidtable = None
    self.prune = True
    self.glyphs = set()
    self._invert_y = []
    self._mirror_text = mirror_text
    self._bounds = None
    self._wks_bounds = None
    # Stack of lists of transforms, where each transform is ('op', parameters)
    self._transforms = []
    self._mirrorstate = []
    self._rotatestate = []
    self._animate = []
    self.colormap = themes.get(theme)
    self.bgcolor = self.color(bgcolor)[0]
    self.prop_display = "VALUE"
    # Deal with auto-animations
    self._has_animation = False
    self._auto_animate = []
    self._animate_attrs = []
    if auto_animate:
      self._animate_attrs = [
        f'begin="svg{id(self):X}.begin"',
        f'dur="{auto_animate[0]:d}s"',
        'fill="freeze"',
      ]
      # Looper
      self._auto_animate = [
        "<animate",
        f'id="svg{id(self):X}"',
        f'dur="{sum(auto_animate):d}s"',
        'attributeName="visibility"',
        f'begin="0;svg{id(self):X}.end"/>',
      ]
    # Metadata tracking
    self.metadata_context = None
    # Each entry is a tuple of (self.metadata_context, text)
    self.generic_text = []
    self.pin_text = []

  def getuid(self, obj):
    """Returns an instance-unique ID for the object.
    If uidtable is set, the ID will be sequential. If multiple SVGs are to be
    used in the same doc, make sure to set uidtable to point to the same list.
    If uidtable is None, returns a non-sequential but likely unique ID.
    """
    if self.uidtable is None:
      return f"{id(obj):X}"
    try:
      return self.uidtable.index(id(obj)) + 1
    except ValueError:
      self.uidtable.append(id(obj))
      return len(self.uidtable)

  def _apply_transforms(self, pos):
    # Apply the stack of transformations back-to-front
    pos = (float(pos[0]), float(self.y(pos[1])))
    for batch in self._transforms[::-1]:
      for transform in batch[::-1]:
        if transform[0] == "translate":
          pos = (pos[0] + transform[1], pos[1] + transform[2])
        elif transform[0] == "scale":
          pos = (pos[0] * transform[1], pos[1] * transform[2])
        elif transform[0] == "rotate":
          cos = math.cos(math.radians(transform[1]))
          sin = math.sin(math.radians(transform[1]))
          pos = (pos[0] * cos - pos[1] * sin, pos[1] * cos + pos[0] * sin)
        elif transform[0] not in ("hide", "noop"):
          raise Exception(f"unrecognized transform {transform}")
    return pos

  def _update_bounds(self, pos, pos2=None, thick=0):
    if self._prune(force=True):
      return
    pos = self._apply_transforms(pos)
    if self._bounds is None:
      self._bounds = pos * 2
    thick = float(thick)
    self._bounds = (
      min(self._bounds[0], pos[0] - thick / 2),
      min(self._bounds[1], pos[1] - thick / 2),
      max(self._bounds[2], pos[0] + thick / 2),
      max(self._bounds[3], pos[1] + thick / 2),
    )
    if pos2 is not None:
      self._update_bounds(pos2, thick=thick)

  def _prune(self, force=False):
    """Returns true if the element should be pruned."""
    return (self.prune or force) and any(
      t[0][0] == "hide" for t in self._transforms if t
    )

  def add(self, line, extend=False):
    """Adds one line to the SVG. Lists are combined with spaces.
    Returns self so you can chain with hascontents or nocontents if desired.
    """
    if self._prune():
      return self
    if not isinstance(line, str):
      line = " ".join(line)
    if extend:
      self.data[-1] += line
    else:
      self.data.append(line)
    return self

  def attr_opacity(self, cnt, i, name="opacity"):
    """Generates an opacity attribute, if cnt > 1"""
    if len(cnt) == 1:
      return []
    c = cnt[i][1] if i else " ".join((c for _, c in cnt[1:] if c))
    return self.attr(
      name, [(1 * (i == 0), None), (1 * (i > 0), c)], 1, convert=False
    )

  def attr(self, name, value=None, default="", i=0, convert=True):
    """Generates an XML attribute and queues an animation if there is more than
    one value. Skips outputting the attribute if it equals default.
    """
    for newvalue, c in value[1:]:
      if value[0][0] != newvalue:
        self._animate.append((name, Svg.tounit(value[0][0]), newvalue, c))
    val = Param.ify(value).get(i)[0]
    if name in Svg.TRANSFORM_TYPES:
      return [f'transform="{name}({",".join(map(str, val))})"'] * any(val)
    elif convert:
      val = Svg.tounit(val)
      default = Svg.tounit(default)
    return [f'{name}="{val}"'] * (str(val) != str(default))

  def hascontents(self, contents=""):
    """Ends the last tag without closing it, and flushes animation tags.
    You will need to provide the closing tag yourself.
    Assumes the last tag does not end in >
    Optionally provide contents to be appended to the line
    """
    if self._prune():
      contents = ""
    else:
      self.data[-1] += ">"
    self._flush_animate()
    if not isinstance(contents, str):
      contents = " ".join(contents)
    self.data[-1] += contents

  def nocontents(self):
    """Ends and closes the last tag, flushing animations in the process.
    Assumes the last tag does not end in />
    """
    if not self._animate:
      if not self._prune():
        self.data[-1] += "/>"
      return
    tag = self.data[-1].partition(" ")[0][1:]
    self.hascontents()
    self.add(f"</{tag}>")

  def _flush_animate(self):
    """Outputs all queued animate tags."""
    for name, fromval, toval, c in self._animate:
      self._has_animation = True
      if name in Svg.TRANSFORM_TYPES:
        params = [
          "<animateTransform",
          'attributeName="transform"',
          f'type="{name}"',
          f'from="{",".join(map(str, fromval))}"',
          f'to="{",".join(map(str, toval))}"',
        ]
      elif name in Svg.COLOR_TYPES:
        # Can't animate CSS Variables, so resolve them
        params = [
          "<animate",
          f'attributeName="{name}"',
          f'fromvar="{fromval}"',
          f'tovar="{toval}"',
        ]
      else:
        params = [
          "<animate",
          f'attributeName="{name}"',
          f'from="{fromval}"',
          f'to="{toval}"',
        ]
      self.add(params + self._animate_attrs + [f'class="{c}"/>'])
    self._animate = []

  def gstart(
    self, pos=None, rotate=None, mirror=False, hidden=False, path=None, tag=None
  ):
    """Starts a group, optionally with coordinate offset."""
    transform = []
    hidden = Param.ify(hidden, False)
    path = Param.ify(path, "")
    if all((h for h, _ in hidden)):
      # Prune this and all subsequent elements
      transform.append(("hide",))
      # If pruning is off, simplify the g tag.
      prune = ["hidden"]
      opacity = Param.ify(1)
    else:
      prune = []
      opacity = [(1 * (not h), c) for h, c in hidden]
    # adds in this function should be pruned, so append the transform stack now
    self._transforms.append(transform)
    pos = Param.ify(pos, (0, 0))
    rotate = Param.ify(rotate, 0)
    mirror = Svg._mirror(mirror)
    if not prune and (len(pos) > 1 or pos[0][0] != (0, 0)):
      transform.append(
        ("translate", float(pos[0][0][0]), float(self.y(pos[0][0][1])))
      )
      self.add(
        ["<g"]
        + Svg._tagattr(tag)
        + self.attr("p", path, "")
        + self.attr(
          "translate",
          [((Svg.tounit(p[0]), Svg.tounit(self.y(p[1]))), c) for p, c in pos],
        )
        + self.attr("opacity", opacity, 1)
      ).hascontents()
      path = Param.ify("")
      opacity = Param.ify(1)
    if not prune and (len(mirror) > 1 or mirror[0][0] != (1, 1)):
      transform.append(("scale",) + mirror[0][0])
      self.add(
        ["<g"]
        + Svg._tagattr(tag)
        + self.attr("p", path, "")
        + self.attr("scale", mirror)
        + self.attr("opacity", opacity, 1)
      ).hascontents()
      path = Param.ify("")
      opacity = Param.ify(1)
    if not prune and (len(rotate) > 1 or rotate[0][0]):
      transform.append(("rotate", -rotate[0][0]))
      self.add(
        ["<g"]
        + Svg._tagattr(tag)
        + self.attr("p", path, "")
        + self.attr("rotate", [((-s,), c) for s, c in rotate])
        + self.attr("opacity", opacity, 1)
      ).hascontents()
      path = Param.ify("")
      opacity = Param.ify(1)
    if prune or not transform:
      if not prune and len(opacity) == 1 and not path[0][0] and not tag:
        transform.append(("noop",))
      else:
        self.add(
          ["<g"]
          + Svg._tagattr(tag)
          + prune
          + self.attr("p", path, "")
          + self.attr("opacity", opacity, 1)
        ).hascontents()
    self._rotatestate.append(rotate)
    self._mirrorstate.append(mirror)

  def gend(self):
    """Ends a group started with gstart.
    Returns True if there ended up being content in this tag.
    """
    self._mirrorstate.pop()
    self._rotatestate.pop()
    prune = self._prune()
    transforms = self._transforms.pop()
    if prune or transforms and transforms[0][0] == "noop":
      return not prune
    # If all that's between this end tag and the start tag are a bunch of
    # animations, delete the whole set
    for d in self.data[-1::-1]:
      if d.startswith("<g"):
        while self.data and self.data[-1].startswith("<g"):
          self.data.pop()
        return False
      elif not d.startswith("<animate"):
        break
    self.add("</g>" * max(1, len(transforms)))
    return True

  def astart(self, target):
    self.add(f'<a href="{target}">')

  def aend(self):
    self.add("</a>")

  def line(self, p1=(0, 0), p2=None, color="wire", thick="wire", pattern=None):
    p1 = Param.ify(p1, (0, 0))
    p2 = Param.ify(p2, (0, 0))
    # FIXME: don't emit anything if color is none?
    color, opacity = self._color(color, "wire")
    thick = Svg._thick(thick)
    pattern = Param.ify(pattern)
    pattern = [
      (
        Svg.pattern(pattern.get(i)[0], thick.get(i)[0]),
        Svg.classunion(pattern.get(i), thick.get(i)),
      )
      for i in range(max(map(len, (pattern, thick))))
    ]
    self._update_bounds(p1[0][0], p2[0][0], thick[0][0])
    self.add(
      ["<line"]
      + self.attr("x1", [(p[0], c) for p, c in p1], 0)
      + self.attr("y1", [(self.y(p[1]), c) for p, c in p1], 0)
      + self.attr("x2", [(p[0], c) for p, c in p2], 0)
      + self.attr("y2", [(self.y(p[1]), c) for p, c in p2], 0)
      + self.attr("stroke", color)
      + self.attr("stroke-opacity", opacity, 1, convert=False)
      + self.attr("stroke-dasharray", pattern)
      + self.attr("stroke-width", thick, Svg.THICKNESS["wire"])
    ).nocontents()

  def rect(
    self,
    pos=(0, 0),
    width=None,
    height=None,
    end=None,
    color="notes",
    fill=None,
    thick="wire",
    pattern=None,
    tag=None,
  ):
    pos = Param.ify(pos, (0, 0))
    if end is not None:
      end = Param.ify(end)
      classes = [
        Svg.classunion(end.get(i), pos.get(i))
        for i in range(max(map(len, (pos, end))))
      ]
      width = [
        (max(abs(end.get(i)[0][0] - pos.get(i)[0][0]), 1 / Svg.SCALE), c)
        for i, c in enumerate(classes)
      ]
      height = [
        (max(abs(end.get(i)[0][1] - pos.get(i)[0][1]), 1 / Svg.SCALE), c)
        for i, c in enumerate(classes)
      ]
      x = [
        (min(end.get(i)[0][0], pos.get(i)[0][0]), c)
        for i, c in enumerate(classes)
      ]
      y = [
        (min(self.y(end.get(i)[0][1]), self.y(pos.get(i)[0][1])), c)
        for i, c in enumerate(classes)
      ]
    else:
      x = [(p[0], c) for p, c in pos]
      y = [(self.y(p[1]), c) for p, c in pos]
      width = Param.ify(width)
      height = Param.ify(height)
    # FIXME: don't emit anything if color and fill are none?
    color, opacity = self._color(color, "notes")
    fill, fillopacity = self._fill(fill, color, opacity)
    thick = Svg._thick(thick)
    pattern = Param.ify(pattern)
    pattern = [
      (
        Svg.pattern(pattern.get(i)[0], thick.get(i)[0]),
        Svg.classunion(pattern.get(i), thick.get(i)),
      )
      for i in range(max(map(len, (pattern, thick))))
    ]
    self._update_bounds(
      pos[0][0],
      (
        float(pos[0][0][0]) + float(width[0][0]),
        float(pos[0][0][1]) + float(self.y(height[0][0])),
      ),
      thick[0][0],
    )
    self.add(
      ["<rect"]
      + self.attr("x", x, 0)
      + self.attr("y", y, 0)
      + self.attr("width", width, 0)
      + self.attr("height", height, 0)
      + self.attr("stroke", color)
      + self.attr("stroke-dasharray", pattern)
      + self.attr("stroke-opacity", opacity, 1, convert=False)
      + self.attr("fill", fill, "none")
      + self.attr("fill-opacity", fillopacity, 1, convert=False)
      + self.attr("stroke-width", thick, Svg.THICKNESS["wire"])
      + Svg._tagattr(tag)
    ).nocontents()

  def circle(
    self,
    pos=(0, 0),
    radius=None,
    color="notes",
    fill=None,
    thick="wire",
    pattern=None,
  ):
    pos = Param.ify(pos, (0, 0))
    radius = Param.ify(radius)
    if any(r[0] < 0 for r in radius):
      raise Exception("negative radius")
    # FIXME: don't emit anything if color and fill are none?
    color, opacity = self._color(color, "notes")
    fill, fillopacity = self._fill(fill, color, opacity)
    thick = Svg._thick(thick)
    pattern = Param.ify(pattern)
    pattern = [
      (
        Svg.pattern(pattern.get(i)[0], thick.get(i)[0]),
        Svg.classunion(pattern.get(i), thick.get(i)),
      )
      for i in range(max(map(len, (pattern, thick))))
    ]
    self._update_bounds(
      (pos[0][0][0] - radius[0][0], pos[0][0][1] - radius[0][0]),
      (pos[0][0][0] + radius[0][0], pos[0][0][1] + radius[0][0]),
      thick[0][0],
    )
    self.add(
      ["<circle"]
      + self.attr("cx", [(p[0], c) for p, c in pos], 0)
      + self.attr("cy", [(self.y(p[1]), c) for p, c in pos], 0)
      + self.attr("r", radius, 0)
      + self.attr("stroke", color)
      + self.attr("stroke-dasharray", pattern)
      + self.attr("stroke-opacity", opacity, 1, convert=False)
      + self.attr("fill", fill, "none")
      + self.attr("fill-opacity", fillopacity, 1, convert=False)
      + self.attr("stroke-width", thick, Svg.THICKNESS["wire"])
    ).nocontents()

  @staticmethod
  def c_r_la(a, b, c):
    # Returns the center, radius, and largearc flag from a set of three points
    # from https://math.stackexchange.com/a/3503338
    z1, z2, z3 = complex(*a), complex(*b), complex(*c)
    w = (z3 - z1) / (z2 - z1)
    c = (z2 - z1) * (w - abs(w) ** 2) / (2j * w.imag) + z1
    r = abs(z1 - c)
    la = (z3 - z1).real * (c - z1).imag - (z3 - z1).imag * (c - z1).real < 0
    return (c.real, c.imag), r, la

  def arc(
    self,
    start,
    stop,
    mid=None,
    radius=None,
    largearc=None,
    color="notes",
    fill=None,
    thick="wire",
    pattern=None,
  ):
    start = Param.ify(start)
    stop = Param.ify(stop)
    center = None
    if mid is not None:
      mid = Param.ify(mid)
      crlas = [
        Svg.c_r_la(start.get(i)[0], mid.get(i)[0], stop.get(i)[0])
        for i in range(max(map(len, (start, stop, mid))))
      ]
      center = Param.ify(
        [
          (crlas[i][0], Svg.classunion(start.get(i), stop.get(i), mid.get(i)))
          for i in range(len(crlas))
        ]
      )
      radius = Param.ify(
        [
          (crlas[i][1], Svg.classunion(start.get(i), stop.get(i), mid.get(i)))
          for i in range(len(crlas))
        ]
      )
      largearc = Param.ify(
        [
          (crlas[i][2], Svg.classunion(start.get(i), stop.get(i), mid.get(i)))
          for i in range(len(crlas))
        ]
      )
    else:
      assert radius and largearc
      radius = Param.ify(radius)
      largearc = Param.ify(largearc)
    # FIXME: don't emit anything if color and fill are none?
    color, opacity = self._color(color, "notes")
    fill, fillopacity = self._fill(fill, color, opacity)
    thick = Svg._thick(thick)
    pattern = Param.ify(pattern)
    pattern = [
      (
        Svg.pattern(pattern.get(i)[0], thick.get(i)[0]),
        Svg.classunion(pattern.get(i), thick.get(i)),
      )
      for i in range(max(map(len, (pattern, thick))))
    ]
    d = [
      (
        " ".join(
          (
            "M",
            Svg.tounit(start.get(i)[0][0]),
            Svg.tounit(self.y(start.get(i)[0][1])),
            "A",
            Svg.tounit(radius.get(i)[0]),
            Svg.tounit(radius.get(i)[0]),
            "0",
            str(1 * largearc.get(i)[0]),
            str(int(0.5 + self.y(0.5))),  # flip sweep dir when y is flipped
            Svg.tounit(stop.get(i)[0][0]),
            Svg.tounit(self.y(stop.get(i)[0][1])),
          )
          + (
            "L",
            Svg.tounit(center.get(i)[0][0]),
            Svg.tounit(self.y(center.get(i)[0][1])),
            "Z",
          )
          * (center and fill.get(i)[0] != "none")
        ),
        Svg.classunion(
          start.get(i), stop.get(i), radius.get(i), largearc.get(i), fill.get(i)
        ),
      )
      for i in range(max(map(len, (start, stop, radius, largearc, fill))))
    ]
    # FIXME: this is wrong but it doesn't really matter that much
    self._update_bounds(start[0][0], stop[0][0], thick[0][0])
    self.add(
      ["<path"]
      + self.attr("d", d)
      + self.attr("fill", fill, "none")
      + self.attr("fill-opacity", fillopacity, 1, convert=False)
      + self.attr("stroke", color)
      + self.attr("stroke-dasharray", pattern)
      + self.attr("stroke-opacity", opacity, 1, convert=False)
      + self.attr("stroke-width", thick, Svg.THICKNESS["wire"])
    ).nocontents()

  def polyline(self, xys, color="notes", fill=None, thick="wire", pattern=None):
    """Renders a polyline.
    xys should be a list of tuples of coordinates, or a Param of such.
    """
    # Ensure xys is in the right format. Should be [([(
    if not isinstance(xys, Param):
      assert isinstance(xys[0], (list, tuple))
      if isinstance(xys[0][0], (list, tuple)):
        assert isinstance(xys[0][0][0], tuple)
      else:
        xys = [(xys, None)]
    d = [
      (
        " ".join(
          f"{'L' if i else 'M'} {Svg.tounit(pt[0])} {Svg.tounit(self.y(pt[1]))}"
          for i, pt in enumerate(pts)
        ),
        c,
      )
      for pts, c in xys
    ]
    # FIXME: don't emit anything if color and fill are none?
    color, opacity = self._color(color, "notes")
    fill, fillopacity = self._fill(fill, color, opacity)
    thick = Svg._thick(thick)
    pattern = Param.ify(pattern)
    pattern = [
      (
        Svg.pattern(pattern.get(i)[0], thick.get(i)[0]),
        Svg.classunion(pattern.get(i), thick.get(i)),
      )
      for i in range(max(map(len, (pattern, thick))))
    ]
    for pts, _ in xys:
      for pt in pts:
        self._update_bounds(pt, thick=thick[0][0])
    self.add(
      ["<path"]
      + self.attr("d", d)
      + self.attr("fill", fill, "none")
      + self.attr("fill-opacity", fillopacity, 1, convert=False)
      + self.attr("stroke", color)
      + self.attr("stroke-dasharray", pattern)
      + self.attr("stroke-opacity", opacity, 1, convert=False)
      + self.attr("stroke-width", thick, Svg.THICKNESS["wire"])
    ).nocontents()

  def image(self, data, pos=(0, 0), scale=1):
    """Adds an image of specified size, centered around pos."""
    data = Param.ify(data)
    pos = Param.ify(pos, (0, 0))
    scale = Param.ify(scale)
    for i in range(len(data)):
      image, width, height = self._image(data[i][0])
      width = Param.ify([(round(width * float(s)), c) for s, c in scale])
      height = Param.ify([(round(height * float(s)), c) for s, c in scale])
      pos = [
        (
          (
            pos.get(i)[0][0] - width.get(i)[0] // 2,
            pos.get(i)[0][1] - height.get(i)[0] // 2,
          ),
          Svg.classunion(pos.get(i), scale.get(i)),
        )
        for i in range(max(map(len, (pos, scale))))
      ]
      self._update_bounds(
        pos[i][0], (pos[i][0][0] + width[i][0], pos[i][0][1] + height[i][0])
      )
      self.add(
        ["<image", f'href="{image}"']
        + self.attr("x", [(p[0], c) for p, c in pos], 0, i)
        + self.attr("y", [(self.y(p[1]), c) for p, c in pos], 0, i)
        + self.attr("width", width, 0, i)
        + self.attr("height", height, 0, i)
        + self.attr(
          "image-rendering",
          [
            ("pixelated" if s >= self.IMAGE_PIXEL_SCALE_THRESHOLD else "", c)
            for s, c in scale
          ],
          "",
          i,
        )
        + self.attr_opacity(data, i=i)
      ).nocontents()

  def text(
    self,
    text,
    prop="",
    pos=(0, 0),
    size="100%",
    color="notes",
    justify="middle",
    vjustify="middle",
    bold=False,
    italic=False,
    rotate=None,
    hidden=None,
    url=None,
    tag=None,
  ):
    needsgroup = False
    rotate = Param.ify(rotate, 0)
    hidden = Param.ify(hidden, False)
    text = Param.ify(text)
    pos = Param.ify(pos, (0, 0))
    bold = [("bold" if b else "normal", c) for b, c in Param.ify(bold)]
    italic = [("italic" if i else "normal", c) for i, c in Param.ify(italic)]
    size = [(self.size(s), c) for s, c in Param.ify(size)]
    color, opacity = self._color(color, "notes")
    anchor = [(Svg.ANCHOR[str(j).lower()], c) for j, c in Param.ify(justify)]
    ### WORKAROUND for crbug/389845192
    vjustmap = dict(Svg.VJUST)
    if any("~{" in t for t, _ in text):
      vjustmap["middle"] = (vjustmap["middle"][0], "middle")
    ### end workaround
    vjust = [(vjustmap[str(j).lower()], c) for j, c in Param.ify(vjustify)]
    url = Param.ify(url)
    if (
      len(rotate) > 1
      or rotate[0][0]
      or len(hidden) > 1
      or hidden[0][0]
      or len(text) > 1
      or "\n" in text[0][0]
      or len(url) > 1
      or self._mirror_text
    ):
      needsgroup = True
      self.gstart(pos=pos, rotate=rotate, hidden=hidden)
      pos = Param.ify((0, 0))
    # Calculate bounding box
    textpos = (float(pos[0][0][0]), float(pos[0][0][1]))
    linecount = text[0][0].count("\n") + 1
    linewidth = max(len(t) for t in text[0][0].split("\n"))
    textwidth = size[0][0] / Svg.FONT_SIZE * linewidth
    thick = size[0][0] / Svg.FONT_SIZE / 8
    if anchor[0][0] == "middle":
      textpos = (textpos[0] - textwidth / 2, textpos[1])
    elif anchor[0][0] == "end":
      textpos = (textpos[0] - textwidth, textpos[1])
    textpos = (
      textpos[0],
      textpos[1] + self.y(size[0][0] * linecount * vjust[0][0][0]),
    )
    # Reference: gr_text.cpp: reg=size/8, demibold=size/6, bold=size/5
    self._update_bounds(
      textpos,
      (textpos[0] + textwidth, textpos[1] - self.y(size[0][0] * linecount)),
      thick=thick,
    )
    xpos_factor = 1
    if self._mirror_text:
      anchor = [
        ({"start": "end", "end": "start"}.get(a, a), c) for a, c in anchor
      ]
      xpos_factor = -1
    # It is critical that no extraneous newlines exist within <text>, otherwise
    # textContent will be inaccurate. Use extend=True on all calls to Svg.add
    self.add(
      ["<text", 'stroke="none"']
      + self.attr("x", [(p[0] * xpos_factor, c) for p, c in pos], 0)
      + self.attr("y", [(self.y(p[1]), c) for p, c in pos], 0)
      + self.attr("fill", color, "none")
      + self.attr("fill-opacity", opacity, 1, convert=False)
      + self.attr("font-size", size, Svg.FONT_SIZE)
      + self.attr("font-style", italic, "normal")
      + self.attr("font-weight", bold, "normal")
      + self.attr("text-anchor", anchor, "start")
      + ['transform="scale(-1 1)"'] * self._mirror_text
      + Svg._tagattr(tag)
      + ([f'prop="{prop}"'] if prop and isinstance(prop, str) else [])
    ).hascontents()
    # FIXME: clean up this janky way of collecting pin names/numbers
    if prop and isinstance(prop, int) and prop < Svg.PROP_LABEL:
      pintext = "\n".join(t for t, _ in text)
      if not self.pin_text or len(self.pin_text[-1]) == 3:
        self.pin_text.append((self.metadata_context, pintext))
      elif prop == Svg.PROP_PIN_NAME:
        self.pin_text[-1] += (pintext,)
      else:
        self.pin_text[-1] = (
          self.pin_text[-1][0],
          pintext,
          self.pin_text[-1][1],
        )
    elif (
      not tag
      and prop not in self.GENERIC_IGNORE
      and not self._prune(force=True)
    ):
      self.generic_text.append(
        (self.metadata_context, "\n".join(t for t, _ in text))
      )
    for i in range(len(text)):
      self.glyphs.update(text.get(i)[0])
      if url.get(i)[0]:
        targ = " target='_blank'" * (not url.get(i)[0].startswith("#"))
        self.add([f"<a href='{url.get(i)[0]}'{targ}>"], extend=True)
      opacity = (
        []
        if len(text) == 1
        else self.attr_opacity(text, i=i, name="fill-opacity")
      )
      baseline = self.attr(
        "dominant-baseline", [(vj[1], c) for vj, c in vjust], "baseline"
      )
      # KiCad ignores trailing newlines
      splittext = text[i][0].rstrip("\n").split("\n")
      for lineno, line in enumerate(splittext):
        colcount = 0
        for colno, t in enumerate(line.split("\t")):
          encoded = Svg.encode(t or "") or " "
          yattr = (
            self.attr(
              "y",
              [
                (f"{(len(splittext) - 1) * (vj[0] - 1):g}em", c)
                for vj, c in vjust
              ],
              "-0em",
            )
            if len(splittext) > 1 and not lineno
            else []
          )
          self.add(
            ["<tspan"]
            + ['x="0"', 'dy="1em"'] * (lineno > 0) * (colno == 0)
            + [f'x="{(colcount // 4 + 1) * 4 * (1 + Svg.FONT_SPACING):g}ex"']
            * (colno > 0)
            + yattr
            + baseline
            + opacity,
            extend=True,  # stray whitespace in <text> causes misalignment
          ).hascontents(f"{encoded}</tspan>")
          colcount += len(t)
      if url.get(i)[0]:
        self.add("</a>", extend=True)
    self.add("</text>", extend=True)
    if needsgroup:
      self.gend()

  def title(self, label):
    self.add(f"<title>{Svg.escape(label)}</title>")

  def instantiate(self, draw, lib, lib_id, unit=1, variant=1, context=None):
    """Instantiates a symbol. lib must contain a definition of lib_id.
    Returns True if the symbol was successfully instantiated; otherwise you
    should draw something yourself.
    """
    lib = Param.ify(lib)
    lib_id = Param.ify(lib_id)
    unit = Param.ify(unit)
    variant = Param.ify(variant)
    name = [
      (
        ":".join(
          (
            "symbol",
            f"{lib.get(i)[0].sym_hash(lib_id.get(i)[0]):x}",
            str(unit.get(i)[0]),
            str(variant.get(i)[0]),
            str(self._rotate_state(i) // 90),
            "m" * self._mirror_state(i),
            f"{draw:x}",
          )
        ),
        Svg.classunion(lib.get(i), lib_id.get(i), unit.get(i), variant.get(i)),
      )
      for i in range(max(map(len, (lib_id, lib, unit, variant))))
    ]
    for i in range(len(name)):
      if name[i][0] in self.symbols:
        continue
      params = name[i][0].split(":")
      sym = lib.get(i)[0].hash_lookup(params[1])
      assert sym is not None
      symsvg = Svg(
        self.bgcolor,
        header=False,
        auto_animate=False,
        mirror_text=self._mirror_state(i),
      )
      symsvg.push_invert_y()
      symsvg.colormap = self.colormap
      sym.fillsvg(
        symsvg,
        [],
        draw,
        context or (),
        unit=int(params[2]),
        variant=int(params[3]),
      )
      symsvg.pop_invert_y()
      self.symbols[name[i][0]] = symsvg
    return self._instantiate(name)

  def instantiate_worksheet(self, draw, context, worksheet=None):
    """Instantiates a worksheet, based on the context."""
    # FIXME: handle page size/variable/worksheet changes?
    wks = Param.ify(worksheet or self.worksheet)
    context = Param.ify(context)
    name = [
      (
        None
        if wks.get(i)[0] is None
        else f"wks:{wks.get(i)[0].wks_hash(context.get(i)[0]):x}",
        Svg.classunion(wks.get(i), context.get(i)),
      )
      for i in range(max(map(len, (wks, context))))
    ]
    # keep the worksheet bounds separate
    orig_bounds, self._bounds = self._bounds, self._wks_bounds
    if draw & Drawable.DRAW_WKS:
      for i in range(len(name)):
        if not name[i][0] or name[i][0] in self.symbols:
          continue
        wkssvg = Svg(self.bgcolor, header=False, auto_animate=False)
        wkssvg.colormap = self.colormap
        wks.get(i)[0].fillsvg(wkssvg, [], Drawable.DRAW_WKS, context.get(i)[0])
        self.symbols[name[i][0]] = wkssvg
      self._instantiate(name)
    if draw & Drawable.DRAW_WKS_PG:
      for i in range(len(name)):
        if wks.get(i)[0]:
          wks.get(i)[0].fillsvg(
            self, [], Drawable.DRAW_WKS_PG, context.get(i)[0]
          )
    self._wks_bounds, self._bounds = self._bounds, orig_bounds

  def _instantiate(self, name):
    for i in range(len(name)):
      symsvg = self.symbols.get(name[i][0])
      if not symsvg:
        if i == len(name) - 1:
          return False
        continue
      if symsvg.data:
        self.add(
          ["<use", f'href="#{name[i][0]}"'] + self.attr_opacity(name, i=i)
        ).nocontents()
        self.generic_text.extend(
          (self.metadata_context, t) for _, t in symsvg.generic_text
        )
        self.pin_text.extend(
          (self.metadata_context,) + t[1:] for t in symsvg.pin_text
        )
        self.glyphs.update(symsvg.glyphs)
        bounds = symsvg._bounds
        self._update_bounds(
          (bounds[0], self.y(bounds[1])), (bounds[2], self.y(bounds[3]))
        )
    return True

  def _mirror_state(self, i=0):
    mirror_state = False
    for mirror in self._mirrorstate:
      if mirror.get(i)[0][0] != mirror.get(i)[0][1]:
        mirror_state = not mirror_state
    return mirror_state

  def _rotate_state(self, i=0):
    rotate_state = 0
    for rotate in self._rotatestate:
      rotate_state += rotate.get(i)[0] or 0
    # Y mirrors are just X mirrors rotated 180 degrees
    for mirror in self._mirrorstate:
      if mirror.get(i)[0][1] != -1:
        rotate_state += 180
    return rotate_state % 360

  def _image(self, data):
    imagetype, imagedata, w, h = self.imagedata(data)
    b64 = base64.b64encode(imagedata).decode("ascii")
    image = f"data:image/{imagetype};base64,{b64}"
    return (image, w, h)

  @staticmethod
  def imagedata(data, convert_all=False):
    """Returns a tuple of (type, data, width, height) of the specified image.
    Converts bmp files to png. If convert_all is specified, converts jpgs as
    well, although this only works if ImageMagick is installed and in PATH.
    Width and height are in mm, not pixels.
    """
    # FIXME: does KiCad check metadata, or does it use 300 always?
    px_to_mm = 25.4 / 300
    if isinstance(data, str):
      data = base64.b64decode(data)
    for typ, mod in ("png", png), ("bmp", bmp), ("jpeg", jpeg):
      sz = mod.getsize(data)
      if sz is None:
        continue
      sz = (sz[0] * px_to_mm, sz[1] * px_to_mm)
      if hasattr(mod, "to_png"):
        return ("png", mod.to_png(data)) + sz
      elif convert_all and typ != "png":
        ret = subprocess.run(
          ["convert", "-", "png:-"], input=data, capture_output=True
        )
        assert ret.returncode == 0
        return ("png", ret.stdout) + sz
      return (typ, data) + sz
    return None

  @staticmethod
  def _thick(thickparam):
    """Processes a thickness parameter to deal with diffs and animation.
    Returns a Param of svg-compatible stroke widths.
    """
    return Param.ify(
      [
        (Svg.THICKNESS.get("wire" if t is None else t, t), c)
        for t, c in Param.ify(thickparam)
      ]
    )

  @staticmethod
  def _mirror(mirrorparam):
    """Splits a mirror param into scale factors, including diffs.
    Returns a Param of x,y scale tuples
    """
    return Param.ify(
      [
        ((-1 if m == "y" else 1, -1 if m == "x" else 1), c)
        for m, c in Param.ify(mirrorparam)
      ]
    )

  def _color(self, colorparam, default):
    """Processes a color parameter to deal with diffs and animation.
    Returns a tuple of Param of svg-compatible colors and Param of opacities.
    """
    colors = []
    opacities = []
    for color, cl in Param.ify(colorparam):
      color, opacity = self.color(color or default)
      colors.append((color, cl))
      opacities.append((opacity, cl))
    return (Param.ify(colors), Param.ify(opacities))

  def _fill(self, fillparam, color, opacity):
    """Processes a fill parameter to deal with cases where the fill should equal
    the color, as well as diffs and animations.
    Returns a tuple of Param of svg-compatible colors and Param of opacities.
    """
    fillparam = Param.ify(fillparam)
    if len(fillparam) == 1 and len(color) > 1 and fillparam[0][0] is True:
      fillparam = fillparam[0:1] * len(color)
    colors = []
    opacities = []
    for i in range(len(fillparam)):
      fill, c = fillparam[i]
      if not fill:
        colors.append(("none", c))
        opacities.append((1, c))
      elif fill == "outline":
        color_i, color_c = color.get(i)
        opacity_i, opacity_c = opacity.get(i)
        assert color_c == opacity_c
        colors.append((color_i, color_c))
        opacities.append((opacity_i, opacity_c))
      else:
        color, opacity = self.color(fill)
        colors.append((color, c))
        opacities.append((opacity, c))
    return (Param.ify(colors), Param.ify(opacities))

  @staticmethod
  def _tagattr(tag):
    # Returns a tag attribute in a list
    if not tag:
      return []
    if isinstance(tag, int):
      return [f'tag="{tag:x}"']
    return [f'tag="{tag}"']

  def color(self, color):
    """Maps a CAD tool color to an SVG-compatible color."""
    if isinstance(self.colormap, str):
      return self.colormap
    if isinstance(color, str):
      color = color.lower()
    while color in self.colormap:
      color = self.colormap[color]
    opacity = 1
    # Convert color to svg format and split into color + opacity
    if isinstance(color, tuple):
      if len(color) > 3:
        opacity = color[3]
      color = "#" + "".join(f"{c:02X}" for c in color[:3])
    return (color, opacity)

  def size(self, size):
    """Maps a size to the unit size of the font."""
    size = 1.0 if size is None else size
    if isinstance(size, str) and size.endswith("%"):
      size = float(size[:-1]) / 100
    return Svg.FONT_SIZE * float(size)

  @staticmethod
  def pattern(pattern, thick):
    """Maps a name or number to an SVG pattern."""
    if pattern is None:
      return ""
    assert pattern in Svg.PATTERNS
    return ",".join(
      Svg.tounit(Svg.PATTERN_SCALE * float(thick) * c)
      for c in Svg.PATTERNS[pattern]
    )

  @staticmethod
  def escape(text):
    """Escapes text such that it can be displayed.
    This is both special character escaping as well as converting leading
    spaces, trailing spaces, and pairs of spaces to use non-breaking spaces (to
    prevent spaces from being collapsed). Trailing spaces need to be handled in
    the case of right- and center-justification."""
    text = escape(text)
    if text.startswith(" "):
      text = "&#160;" + text[1:]
    if text.endswith(" "):
      text = text[:-1] + "&#160;"
    return text.replace("  ", " &#160;")

  @staticmethod
  def encode(text):
    """adds <tspan> elements for any embedded formatting"""
    # NOTE: remember to update diffui.js's generic text matcher to reflect this
    # XML is OK with _^~{}, so it's safe (and required) to escape first
    text = text.replace("{slash}", "/")
    text = Svg.escape(text)
    text = Svg._ENCODE_BLOCKS_RE.sub(Svg._encode_block, text)
    return text

  _ENCODE_BLOCKS_RE = re.compile(r"[_^~]\{((?:[^{}]|\{[^}]*\})*)\}")

  @staticmethod
  def _encode_block(text):
    """Encodes a single formatting block in a string"""
    # Handle a little bit of nesting (not full support)
    innertext = Svg._ENCODE_BLOCKS_RE.sub(Svg._encode_block, text[1])
    # Reference: STROKE_FONT::GetTextAsGlyphs
    if text[0][0] == "_":  # subscript
      return f'<tspan font-size="80%" baseline-shift="-15%">{innertext}</tspan>'
    elif text[0][0] == "^":  # superscript
      return f'<tspan font-size="80%" baseline-shift="35%">{innertext}</tspan>'
    elif text[0][0] == "~":  # overbar
      return f'<tspan text-decoration="overline">{innertext}</tspan>'
    raise AssertionError()  # regex is bad

  @staticmethod
  def classunion(*classstrings):
    # Used to combine diff ids together so that either diff will trigger
    return " ".join(
      sorted(
        {
          c
          for cs in classstrings
          for cl in ((cs if isinstance(cs, str) else cs[1]) or "").split(" ")
          for c in cl
          if c
        }
      )
    )

  @staticmethod
  def tomm(coord):
    return f"{coord:.4f}mm"

  @staticmethod
  def tounit(mm):
    return mm if isinstance(mm, str) else str(round(mm * Svg.SCALE))

  def y(self, mm):
    return -mm if self._invert_y and self._invert_y[-1] else mm

  def push_invert_y(self, invert=True):
    self._invert_y.append(invert)

  def pop_invert_y(self):
    return self._invert_y.pop()

  @staticmethod
  def _get_placeholder():
    if not isinstance(Svg._PLACEHOLDER, Svg):
      placeholder = Svg()
      placeholder.text(Svg._PLACEHOLDER[0], color="device")
      placeholder._bounds = (
        -(Svg._PLACEHOLDER[1] + 0) * placeholder._bounds[2],
        (Svg._PLACEHOLDER[2] + 0) * placeholder._bounds[1],
        (Svg._PLACEHOLDER[1] + 1) * placeholder._bounds[2],
        -(Svg._PLACEHOLDER[2] + 1) * placeholder._bounds[1],
      )
      Svg._PLACEHOLDER = placeholder
    return Svg._PLACEHOLDER

  def get_viewbox(self, convert=True, with_wks=True):
    orig_bounds = self._bounds
    if with_wks and self._wks_bounds:
      self._update_bounds(self._wks_bounds[:2], self._wks_bounds[2:])
    if not self._bounds:
      return Svg._get_placeholder().get_viewbox(convert)
    box = (
      self._bounds[0],
      self._bounds[1],
      self._bounds[2] - self._bounds[0],
      self._bounds[3] - self._bounds[1],
    )
    if convert:
      box = tuple(map(Svg.tounit, box))
    self._bounds = orig_bounds
    return box

  def __repr__(self):
    """Returns a string of the SVG"""
    svg = []
    if self.header:
      svg.append('<?xml version="1.0"?>')
      svg.append(
        '<!DOCTYPE svg PUBLIC "-//W3C//DTD SVG 1.1//EN"'
        + ' "http://www.w3.org/Graphics/SVG/1.1/DTD/svg11.dtd">'
      )
    viewbox = self.get_viewbox(convert=False)
    svg.append(
      " ".join(
        (
          '<svg xmlns="http://www.w3.org/2000/svg"',
          'xmlns:xlink="http://www.w3.org/1999/xlink"',
          f'viewBox="{",".join(tuple(map(Svg.tounit, viewbox)))}"',
          f'width="{Svg.tomm(viewbox[2])}" height="{Svg.tomm(viewbox[3])}"',
          'fill="none"',
          f'font-family="{Svg.FONT_FAMILY}"',
          f'font-size="{Svg.tounit(Svg.FONT_SIZE)}"',
          f'font-stretch="{Svg.FONT_CONDENSE * 100:g}%"',
          f'letter-spacing="{Svg.FONT_SPACING:g}ex"',
          f'stroke-width="{Svg.tounit(Svg.THICKNESS["wire"])}"',
          'stroke-linecap="round"',
          'stroke-linejoin="round"',
          f'style="background-color:{self.bgcolor}"',
        )
      )
      + ">"
    )
    if self._has_animation:
      svg += self._auto_animate
    # Add all symbols
    for name, symsvg in self.symbols.items():
      if symsvg is None or not symsvg.data:
        continue
      svg.append(f'<symbol id="{name}" overflow="visible">')
      svg += symsvg.data
      svg.append("</symbol>")
    svg += self.data or Svg._get_placeholder().data
    svg.append("</svg>\n")
    return "\n".join(svg)
