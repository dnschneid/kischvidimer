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
import math
import os
import subprocess
import sys
from xml.sax.saxutils import escape

import git
from filetypes import sym
from diff import Param
import bmp


class Svg(object):
  """Helps generate an SVG image.
  """

  ANCHOR = {}
  # Scale the aspect of the SVG stipple pattern by changing the constant
  PATTERN_SCALE = 0
  PATTERNS = {
  }
  # SVG thicknesses
  THICKNESS = {
    'thin':  0,
    'thick': 0,
  }
  FONT_SIZE = 0
  # Transformation types
  TRANSFORM_TYPES = ()
  # Color types
  COLOR_TYPES = ()
  # Properties of text that are indexed outside of generic text
  GENERIC_IGNORE = ()
  # Text to render and horiz/vert margin if there is no content
  _PLACEHOLDER = ('Empty file', 5, 10)

  def __init__(self, bgcolor='white', header=True, autoAnimate=(2, 1),
               rotate_text=False, mirror_text=False):
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
    self.datadir = None
    self.font_size = 1.0
    self.uidtable = None
    self._rotate_text = rotate_text
    self._mirror_text = mirror_text
    self._bounds = None
    # Stack of lists of transforms, where each transform is ('op', parameters)
    self._transforms = []
    self._flipstate = []
    self._rotatestate = []
    self._animate = []
    self.colormap = {
        # Keyword -> default color mapping
        # Add shapes
        # Default colors
        # Add colors for geometries 
        # Colors that need to be translated to css vars
        'RED':'var(--r)',
        'GREEN':'var(--g)',
        'BLUE':'var(--u)',
        'YELLOW':'var(--y)',
        'ORANGE':'var(--o)',
        'MONO':'var(--k)',
        'SALMON':'var(--n)',
        'VIOLET':'var(--v)',
        'BROWN':'var(--x)',
        'SKYBLUE':'var(--b)',
        'WHITE':'var(--w)',
        'PEACH':'var(--h)',
        'PINK':'var(--p)',
        'PURPLE':'var(--l)',
        'AQUA':'var(--q)',
        'GRAY':'var(--a)'
    }
    self.bgcolor = 'var(--c)'
    self.prop_display = 'VALUE'
    # Deal with auto-animations
    self._hasAnimation = False
    self._autoAnimate = []
    self._animateAttrs = []
    if autoAnimate:
      self._animateAttrs = [
        'begin="svg%X.begin"' % (id(self)),
        'dur="%us"' % autoAnimate[0],
        'fill="freeze"',
        ]
      # Looper
      self._autoAnimate = ['<animate',
        'id="svg%X"' % id(self),
        'dur="%us"' % sum(autoAnimate),
        'attributeName="visibility"',
        'begin="0;svg%X.end"/>' % id(self),
        ]
    self.generic_text = []
    self.pin_text = []

  def getuid(self, obj):
    """Returns an instance-unique ID for the object.
    If uidtable is set, the ID will be sequential. If multiple SVGs are to be
    used in the same doc, make sure to set uidtable to point to the same list.
    If uidtable is None, returns a non-sequential but likely unique ID.
    """
    if self.uidtable is None:
      return '%X' % id(obj)
    try:
      return self.uidtable.index(id(obj)) + 1
    except ValueError:
      self.uidtable.append(id(obj))
      return len(self.uidtable)

  def _apply_transforms(self, pos):
    # Apply the stack of transformations back-to-front
    pos = (pos[0], -pos[1])
    for batch in self._transforms[::-1]:
      for transform in batch[::-1]:
        if transform[0] == 'translate':
          pos = (pos[0] + transform[1], pos[1] + transform[2])
        elif transform[0] == 'scale':
          pos = (pos[0] * transform[1], pos[1] * transform[2])
        elif transform[0] == 'rotate':
          cos = math.cos(math.radians(transform[1]))
          sin = math.sin(math.radians(transform[1]))
          pos = (pos[0] * cos - pos[1] * sin, pos[1] * cos + pos[0] * sin)
        elif transform[0] not in ('hide', 'noop'):
          raise Exception('unrecognized transform %s' % transform)
    return pos

  def _update_bounds(self, pos, pos2=None):
    if self._prune():
      return
    pos = self._apply_transforms(pos)
    if self._bounds is None:
      self._bounds = pos*2
    else:
      self._bounds = (min(self._bounds[0], pos[0]),
                      min(self._bounds[1], pos[1]),
                      max(self._bounds[2], pos[0]),
                      max(self._bounds[3], pos[1]))
    if pos2 is not None:
      self._update_bounds(pos2)

  def _prune(self):
    """Returns true if the element should be pruned."""
    return (self._transforms and self._transforms[-1]
        and self._transforms[-1][0][0] == 'hide')

  def add(self, line):
    """Adds one line to the SVG. Lists are combined with spaces.
    Returns self so you can chain with hascontents or nocontents if desired.
    """
    if self._prune():
      return self
    if not isinstance(line, str):
      line = ' '.join(line)
    self.data.append(line)
    return self

  def attr_opacity(self, cnt, i, name='opacity'):
    """Generates an opacity attribute, if cnt > 1"""
    if len(cnt) == 1:
      return []
    if i == 0:
      c = ' '.join((c for _,c in cnt[1:] if c))
    else:
      c = cnt[i][1]
    return self.attr(name,
        [(1*(i==0), None), (1*(i>0), c)], 1)

  def attr(self, name, value=None, default='', i=0):
    """Generates an XML attribute and queues an animation if there is more than
    one value. Skips outputting the attribute if it equals default.
    """
    for newvalue, c in value[1:]:
      if value[0][0] != newvalue:
        self._animate.append((name, value[0][0], newvalue, c))
    val = Param.ify(value).get(i)[0]
    if name in Svg.TRANSFORM_TYPES:
      return ['transform="%s(%s)"' % (name, ','.join(map(str, val)))]*any(val)
    else:
      return ['%s="%s"' % (name, val)]*(str(val) != str(default))

  def hascontents(self):
    """Ends the last tag without closing it, and flushes animation tags.
    You will need to provide the closing tag yourself.
    Assumes the last tag does not end in >
    """
    if not self._prune():
      self.data[-1] += '>'
    self._flush_animate()

  def nocontents(self):
    """Ends and closes the last tag, flushing animations in the process.
    Assumes the last tag does not end in />
    """
    if not self._animate:
      if not self._prune():
        self.data[-1] += '/>'
      return
    tag = self.data[-1].partition(' ')[0][1:]
    self.hascontents()
    self.add('</%s>' % tag)

  def _flush_animate(self):
    """Outputs all queued animate tags."""
    for name, fromval, toval, c in self._animate:
      self._hasAnimation = True
      if name in Svg.TRANSFORM_TYPES:
        params = ['<animateTransform',
          'attributeName="transform"',
          'type="%s"' % name,
          'from="%s"' % ','.join(map(str, fromval)),
          'to="%s"' % ','.join(map(str, toval)),
        ]
      elif name in Svg.COLOR_TYPES:
        # Can't animate CSS Variables, so resolve them
        params = ['<animate',
          'attributeName="%s"' % name,
          'fromvar="%s"' % fromval,
          'tovar="%s"' % toval,
        ]
      else:
        params = ['<animate',
          'attributeName="%s"' % name,
          'from="%s"' % fromval,
          'to="%s"' % toval,
        ]
      self.add(params + self._animateAttrs + ['class="%s"/>' % c])
    self._animate = []

  def gstart(self, pos=None, rotate=None, flip=False, hidden=False,
             path=None):
    """Starts a group, optionally with coordinate offset."""
    transform = []
    hidden = Param.ify(hidden, False)
    path = Param.ify(path, '')
    if all((h for h,_ in hidden)):
      # Prune this and all subsequent elements
      transform.append(('hide',))
    # adds in this function should be pruned, so append the transform stack now
    self._transforms.append(transform)
    opacity = [(1*(not h), c) for h,c in hidden]
    pos = Param.ify(pos, (0, 0))
    flip = Param.ify(flip, False)
    rotate = Param.ify(rotate, 0)
    if len(pos) > 1 or pos[0][0] != (0, 0):
      transform.append(('translate', pos[0][0][0], -pos[0][0][1]))
      self.add(['<g']
      + self.attr('p', path, '')
      + self.attr('translate', [((p[0],-p[1]), c) for p,c in pos])
      + self.attr('opacity', opacity, 1)
      ).hascontents()
      path = Param.ify('')
      opacity = Param.ify(1)
    if len(rotate) > 1 or rotate[0][0]:
      transform.append(('rotate', -rotate[0][0]))
      self.add(['<g']
      + self.attr('p', path, '')
      + self.attr('rotate', [((-s,), c) for s,c in rotate])
      + self.attr('opacity', opacity, 1)
      ).hascontents()
      path = Param.ify('')
      opacity = Param.ify(1)
    if len(flip) > 1 or flip[0][0]:
      transform.append(('scale', -1 if flip[0] else 1, 1))
      self.add(['<g']
      + self.attr('p', path, '')
      + self.attr('scale', [((-1 if f else 1, 1), c) for f,c in flip])
      + self.attr('opacity', opacity, 1)
      ).hascontents()
      path = Param.ify('')
      opacity = Param.ify(1)
    if not transform:
      if len(opacity) == 1 and not path[0][0]:
        transform.append(('noop',))
      else:
        self.add(['<g']
        + self.attr('p', path, '')
        + self.attr('opacity', opacity, 1)
        ).hascontents()
    self._rotatestate.append(rotate)
    self._flipstate.append(flip)

  def gend(self):
    """Ends a group started with gstart.
    Returns True if there ended up being content in this tag.
    """
    self._flipstate.pop()
    self._rotatestate.pop()
    prune = self._prune()
    transforms = self._transforms.pop()
    if prune or transforms and transforms[0][0] == 'noop':
      return not prune
    # If all that's between this end tag and the start tag are a bunch of
    # animations, delete the whole set
    for d in self.data[-1::-1]:
      if d.startswith('<g'):
        while not self.data.pop().startswith('<g'):
          pass
        return False
      elif not d.startswith('<animate'):
        break
    self.add('</g>'*max(1, len(transforms)))
    return True

  def astart(self, target):
    self.add('<a href="%s">' % target)

  def aend(self):
    self.add('</a>')

  def line(self, p1=(0,0), p2=None, color='wire', thick='thin', pattern=None):
    p1 = Param.ify(p1, (0, 0))
    p2 = Param.ify(p2, (0, 0))
    color = self._color(color, 'wire')
    thick = Svg._thick(thick)
    pattern = [(Svg.pattern(p), c) for p,c in Param.ify(pattern)]
    self._update_bounds(p1[0][0], p2[0][0])
    self.add(['<line']
    + self.attr('x1', [(p[0],c) for p,c in p1], 0)
    + self.attr('y1', [(-p[1],c) for p,c in p1], 0)
    + self.attr('x2', [(p[0],c) for p,c in p2], 0)
    + self.attr('y2', [(-p[1],c) for p,c in p2], 0)
    + self.attr('stroke', color)
    + self.attr('stroke-dasharray', pattern)
    + self.attr('stroke-width', thick, Svg.THICKNESS['thin'])
    ).nocontents()

  def rect(self, pos=(0,0), width=None, height=None, color='body'):
    pos = Param.ify(pos, (0, 0))
    width = Param.ify(width)
    height = width if height is None else Param.ify(height)
    color = self._color(color, 'body')
    self._update_bounds(pos[0][0],
        (pos[0][0][0]+width[0][0], pos[0][0][1]-height[0][0]))
    self.add(['<rect', 'fill="none"']
    + self.attr('x', [(p[0],c) for p,c in pos], 0)
    + self.attr('y', [(-p[1],c) for p,c in pos], 0)
    + self.attr('width', width, 0)
    + self.attr('height', height, 0)
    + self.attr('stroke', color)
    ).nocontents()

  def circle(self, pos=(0,0), radius=None, color='arc', fill=None,
             thick='thin'):
    pos = Param.ify(pos, (0, 0))
    radius = Param.ify(radius)
    if any(r[0] < 0 for r in radius):
      raise Exception('negative radius')
    color = self._color(color, 'arc')
    fill = self._fill(fill, color)
    thick = Svg._thick(thick)
    self._update_bounds((pos[0][0][0]-radius[0][0], pos[0][0][1]-radius[0][0]),
                        (pos[0][0][0]+radius[0][0], pos[0][0][1]+radius[0][0]))
    self.add(['<circle']
    + self.attr('cx', [(p[0],c) for p,c in pos], 0)
    + self.attr('cy', [(-p[1],c) for p,c in pos], 0)
    + self.attr('r', radius, 0)
    + self.attr('stroke', color)
    + self.attr('fill', fill)
    + self.attr('stroke-width', thick, Svg.THICKNESS['thin'])
    ).nocontents()

  def arc(self, start, stop, radius, largearc, color='arc', thick='thin'):
    start = Param.ify(start)
    stop = Param.ify(stop)
    radius = Param.ify(radius)
    largearc = Param.ify(largearc)
    color = self._color(color, 'arc')
    thick = Svg._thick(thick)
    d = [('M %d %d A %d %d 0 %d 0 %d %d' % (
          start.get(i)[0][0], -start.get(i)[0][1],
          radius.get(i)[0], radius.get(i)[0],
          1*largearc.get(i)[0],
          stop.get(i)[0][0], -stop.get(i)[0][1]),
          ' '.join((c for _,c in (
              start.get(i), stop.get(i), radius.get(i), largearc.get(i)) if c)))
          for i in range(max(map(len, (start, stop, radius, largearc))))]
    # FIXME: this is wrong but it doesn't really matter that much
    self._update_bounds(start[0][0], stop[0][0])
    self.add(['<path', 'fill="none"']
    + self.attr('d', d)
    + self.attr('stroke', color)
    + self.attr('stroke-width', thick, Svg.THICKNESS['thin'])
    ).nocontents()

  def image(self, filename, pos=(0,0), width=None, height=None):
    """Adds an image of specified size, centered around pos."""
    # FIXME: how to handle the case where the filename remains the same but the
    #        data changes between revisions?
    filename = Param.ify(filename)
    pos = Param.ify(pos, (0,0))
    width = Param.ify(width)
    height = Param.ify(height)
    pos = [((pos.get(i)[0][0] - width.get(i)[0]//2,
             pos.get(i)[0][1] + height.get(i)[0]//2),
            ' '.join((c for _,c in (
                pos.get(i), width.get(i), height.get(i)) if c)))
            for i in range(max(map(len, (pos, width, height))))]
    self._update_bounds(pos[0][0],
        (pos[0][0][0]+width[0][0], pos[0][0][1]-height[0][0]))
    for i in range(len(filename)):
      image = self._image(filename[i][0])
      self.add(['<image', 'href="%s"' % image]
      + self.attr('x', [(p[0],c) for p,c in pos], 0, i)
      + self.attr('y', [(-p[1],c) for p,c in pos], 0, i)
      + self.attr('width', width, 0, i)
      + self.attr('height', height, 0, i)
      + self.attr_opacity(filename, i=i)
      ).nocontents()

  def text(self, text, prop='', pos=(0,0), size='100%', color='note',
           justify='LEFT', rotate=None, hidden=None):
    needsgroup = False
    rotate = Param.ify(rotate, 0)
    hidden = Param.ify(hidden, False)
    pos = Param.ify(pos, (0, 0))
    size = [(self.size(s),c) for s,c in Param.ify(size)]
    color = self._color(color, 'note')
    anchor = [(Svg.ANCHOR[j.upper()],c) for j,c in Param.ify(justify)]
    text = Param.ify(text)
    if (len(rotate) > 1 or rotate[0][0] or len(hidden) > 1 or hidden[0][0] or
        len(text) > 1 or self._mirror_text or self._rotate_text):
      needsgroup = True
      self.gstart(pos=pos, rotate=rotate, hidden=hidden)
      pos = Param.ify((0, 0))
    # assume text is 60% as wide as it is tall for the sake of bounding boxes
    textwidth = 0.6 * size[0][0] * len(text[0][0])
    if anchor[0][0] == 'start':
      textpos = pos[0][0]
    elif anchor[0][0] == 'middle':
      textpos = (pos[0][0][0] - textwidth//2, pos[0][0][1])
    elif anchor[0][0] == 'end':
      textpos = (pos[0][0][0] - textwidth, pos[0][0][1])
    self._update_bounds(textpos, (textpos[0]+textwidth, textpos[1]+size[0][0]))
    text = [(Svg.escape(t or ''),c) for t,c in text]
    xpos_factor = 1
    if self._mirror_text != self._rotate_text:
      anchor = [({'start': 'end', 'end': 'start'}.get(a,a),c) for a,c in anchor]
      xpos_factor = -1
    self.add(['<text', 'stroke="none"']
    + self.attr('x', [(p[0]*xpos_factor,c) for p,c in pos], 0)
    + self.attr('y', [(-p[1],c) for p,c in pos], 0)
    + self.attr('fill', color)
    + self.attr('font-size', size, Svg.FONT_SIZE * self.font_size)
    + self.attr('text-anchor', anchor, 'start')
    + ['transform="%s %s"' % ('scale(-1 1)'*self._mirror_text,
        'rotate(180)'*self._rotate_text)]*(self._mirror_text or
          self._rotate_text)
    + (['prop="%s"' % prop] if prop else [])
    ).hascontents()
    if len(text) == 1:
      if prop not in self.GENERIC_IGNORE:
        self.generic_text.append(text[0][0])
      self.data[-1] += '%s</text>' % text[0][0]
    else:
      if prop not in self.GENERIC_IGNORE:
        self.generic_text.append('\n'.join(text[0]))
      for i in range(len(text)):
        self.add(['<tspan', 'x="0"']
        + self.attr_opacity(text, i=i, name='fill-opacity')
        ).hascontents()
        self.data[-1] += '%s</tspan>' % text[i][0]
      self.add('</text>')
    if needsgroup:
      self.gend()

  def title(self, label):
    self.add('<title>%s</title>' % Svg.escape(label))

  def instantiate(self, cell, symbol, color='body', placeholderfunc=None):
    """Instantiates a symbol. Attempts to find the cell and defines it.
    If the cell cannot be found and placeholderfunc is defined, calls
    placeholderfunc with a temporary svg instance to render a placeholder.
    Returns True if the symbol was successfully instantiated; otherwise you
    should draw something yourself.
    """
    cell = Param.ify([(c.lower(),cl) for c,cl in Param.ify(cell)])
    symbol = Param.ify(symbol)
    color = self._color(color, 'body')
    # FIXME: this would be so much simpler if mirror/rotate were a parameter to
    # instantiate. Add handling flipping diffs back in.
    # FIXME: rotate handling still isn't quite right with rotated text in symbols;
    # for some combinations of flip and rotate they can end up 180-rotated.
    # The correct thing to do is to make rotate a parameter to text, and then all
    # these cases can be handled more simply and appropriately.
    name = [('symbol:%s.%s%s' % (
      cell.get(i)[0], symbol.get(i)[0],
      ':m'*self._mirror_state(i) + ':s'*self._rotate_state(i)),
      ' '.join((c for _,c in (
        cell[i] if i < len(cell) else (0,0),
        symbol[i] if i < len(symbol) else (0,0)
        ) if c)))
      for i in range(max(map(len,(symbol,cell))))]
    for i in range(len(name)):
      if name[i][0] in self.symbols:
        continue
      symsvg = None
      for libpath, githash in self._libraries():
        cells = git.listdir(libpath, githash)
        for match in (d for d in cells if d.lower() == cell.get(i)[0]):
          symPath = os.path.join(
              libpath, match, 'symbol_%s' % str(symbol.get(i)[0]), 'symbol.file')
          if git.isfile(symPath, githash):
            symsvg = Svg(self.bgcolor, header=False, autoAnimate=False,
                mirror_text=self._mirror_state(i),
                rotate_text=self._rotate_state(i))
            symsvg.colormap = ''
            symsvg.image_dirs.append(
                (os.path.join(libpath, match, 'images'), githash))
            symfile = git.open_rb(symPath, githash)
            try:
              symFile = sym.sym(symfile, fname=symPath)
            except Exception as e:
              sys.stderr.write('Error loading %s:\n%s\n' % (name[i][0], e))
              symsvg = None
            else:
              pin_texts, generic_texts = symFile.fillsvg(symsvg, sym.sym.MODE_INSTANCE)
              self.generic_text += generic_texts
              self.pin_text += [(cell.get(i)[0], p) for p in pin_texts]
            break
        if symsvg:
          break
      if not symsvg and placeholderfunc:
        symsvg = Svg(self.bgcolor, header=False,
            mirror_text=self._mirror_state(i),
            rotate_text=self._rotate_state(i))
        symsvg.colormap = ''
        placeholderfunc(symsvg)
      self.symbols[name[i][0]] = symsvg
    if self.symbols[name[0][0]]:
      bounds = self.symbols[name[0][0]]._bounds
      self._update_bounds((bounds[0], -bounds[1]), (bounds[2], -bounds[3]))
    for i in range(len(name)):
      if not self.symbols[name[i][0]]:
        if i == len(name)-1:
          return False
        continue
      self.add(['<use', 'href="#%s"' % name[i][0]]
      + self.attr('fill', color)
      + self.attr('stroke', color)
      + self.attr_opacity(name, i=i)
      ).nocontents()
    return True

  def _mirror_state(self, i=0):
    mirror_text = False
    for flip in self._flipstate:
      if flip.get(i)[0]:
        mirror_text = not mirror_text
    return mirror_text

  def _rotate_state(self, i=0):
    rotate_text = 0
    for rotate in self._rotatestate:
      rotate_text += rotate.get(i)[0] or 0
    return 180 <= (rotate_text % 360)

  def _image(self, filename, for_kicad=False):
    # Recover the git hash from the image_dirs database
    # remove whitespace from after filename, common when image was scripted in
    filename = filename.rstrip()
    if '/' in filename or '\\' in filename:
      # In the case where the directory is explicit.
      # pgFile files don't do this, so no need to handle git.
      image = filename
      githash = None
    else:
      for d, githash in self._image_dirs():
        image = os.path.join(d, filename)
        if git.isfile(image, githash):
          break
      else:
        image = os.path.join('../images', filename)
        githash = None
    # Embed the image data if it can be found
    if git.isfile(image, githash):
      imagetype, imagedata = self._imagedata(image, githash, for_kicad)
      if not for_kicad:
        image = 'data:image/%s;base64,%s' % (imagetype,
            base64.b64encode(imagedata).decode('ascii'))
      else:
        image = '\n'.join('%02X' % x for x in imagedata)
    return image

  @staticmethod
  def _imagedata(path, githash=None, convert_all=False):
    """Returns a tuple of (type, data) of the specified image path.
    Converts bmp files to png. If convert_all is specified, converts jpgs as
    well, although this only works if ImageMagick is installed and in PATH.
    """
    imagetype = path.rpartition('.')[2].lower() or 'jpg'
    imagedata = None
    if imagetype == 'bmp':
      imagedata = bmp.to_png(git.open_rb(path, githash))
    elif convert_all:
      # FIXME: how to incorporate git here? maybe it doesn't matter
      imagedata = subprocess.check_output(['convert', path, 'png:-'])
    if imagedata:
      imagetype = 'png'
    else:
      imagedata = git.open_rb(path, githash).read()
    return imagetype, imagedata

  @staticmethod
  def _thick(thickparam):
    """Processes a thickness parameter to deal with diffs and animation.
    Returns a Param of svg-compatible stroke widths.
    """
    return Param.ify([
      (Svg.THICKNESS[t or 'thin'],c) for t,c in Param.ify(thickparam)
    ])

  def _color(self, colorparam, default):
    """Processes a color parameter to deal with diffs and animation.
    Returns a Param of svg-compatible colors.
    """
    return Param.ify([
      (self.color(c or default),cl) for c,cl in Param.ify(colorparam)
    ])

  def _fill(self, fillparam, color):
    """Processes a fill parameter to deal with cases where the fill should equal
    the color, as well as diffs and animations.
    Returns a Param of svg-compatible colors.
    """
    fillparam = Param.ify(fillparam)
    if len(fillparam) == 1 and len(color) > 1 and fillparam[0][0] is True:
      fillparam = fillparam[0:1]*len(color)
    ret = []
    for i in range(len(fillparam)):
      fill, c = fillparam[i]
      if fill is False or fill is None:
        ret.append(('none', c))
      elif fill is True:
        color_i, color_c = color.get(i)
        ret.append((self.color(color_i), color_c))
      else:
        ret.append((self.color(fill), c))
    return ret

  def color(self, color):
    """Maps a CAD tool color to an SVG-compatible color."""
    if isinstance(self.colormap, str):
      return self.colormap
    color = color.upper()
    while color in self.colormap:
      color = self.colormap[color]
    return color.lower()

  def size(self, size):
    """Maps a size to the unit size of the font."""
    size = size or 1.0
    if isinstance(size, str) and size.endswith('%'):
      size = float(size[:-1])/100
    if not isinstance(size, int):
      size = Svg.FONT_SIZE * float(size) * self.font_size
    return size

  @staticmethod
  def pattern(pattern):
    """Maps a name or number to an SVG pattern."""
    if pattern is None:
      return ''
    if pattern in Svg.PATTERNS:
      return ','.join(str(Svg.PATTERN_SCALE * int(c)) for c in
          Svg.PATTERNS[pattern])
    # Convert pattern bitmap to SVG on/off run list
    # FIXME: confirm this actually matches sym patterns
    if isinstance(pattern, str):
      pattern = int(pattern, 0)
    patternlen = 16 if pattern >= 1<<10 else 10
    dasharray = [0]
    for i in range(patternlen):
      bit = (pattern >> i) & 1
      if bit != len(dasharray) % 2:
        dasharray.append(0)
      dasharray[-1] += Svg.PATTERN_SCALE
    if len(dasharray) == 1:
      return ''
    elif len(dasharray) % 2:
      dasharray.append(0)
    return ','.join(map(str, dasharray))

  def _datadir(self):
    """If datadir isn't set, tries to find the project datadir in the current
    working directory. datadir is only needed as a heuristic if svg is expected
    to determine for itself default libraries and image directories.
    """
    if self.datadir:
      return self.datadir
    # Nobody specified datadir, so try to detect something.
    if '/datadir' in os.getcwd():
      self.datadir = ''.join(os.getcwd().rpartition('/datadir')[0:2])
    elif os.path.isdir(os.path.join(os.getcwd(), 'datadir')):
      self.datadir = os.path.join(os.getcwd(), 'datadir')
    else:
      return None
    return self.datadir

  def _libraries(self):
    """Returns a list of directories to be searched in order for a part."""
    if self.libraries is not None:
      return self.libraries
    # Nobody specified any libraries, so load up the default
    self.libraries = []
    datadir = self._datadir()
    cdslib = os.environ.get('Library_File', None)
    for basedir in datadir, cdslib:
      if not basedir or not os.path.isdir(basedir):
        continue
      for lib in os.listdir(basedir):
        path = os.path.join(basedir, lib)
        if os.path.isdir(path):
          self.libraries.append((path, None))
    return self.libraries

  def _image_dirs(self):
    """If self.image_dirs isn't set, returns a list of image directories that
    may be relevant, based on defined libraries or current working directory.
    They may also not be relevant. Automatic detection should only be used if
    image directories can't be determined automatically.
    """
    # FIXME: it may be more correct to only check specific images directories
    if self.image_dirs:
      return self.image_dirs
    # Nobody specified any image dirs, so load up the default
    for libpath, githash in self._libraries():
      for path in git.listdir(libpath, githash):
        path = os.path.join(libpath, path, 'images')
        if git.isdir(path, githash):
          self.image_dirs.append((path, githash))
    # If we didn't find any dirs, add some relative ones
    if not self.image_dirs:
      for path in 'images', '../images':
        if os.path.isdir(path):
          self.image_dirs.append((path, None))
    return self.image_dirs

  @staticmethod
  def escape(text):
    """Escapes text such that it can be displayed.
    This is both special character escaping as well as converting leading
    spaces, trailing spaces, and pairs of spaces to use non-breaking spaces (to
    prevent spaces from being collapsed). Trailing spaces need to be handled in
    the case of right- and center-justification."""
    text = escape(text)
    if text.startswith(' '):
      text = '&#160;' + text[1:]
    if text.endswith(' '):
      text = text[:-1] + '&#160;'
    return text.replace('  ', ' &#160;')

  @staticmethod
  def toinch(coord):
    return '%.5fin' % (coord)

  @staticmethod
  def _get_placeholder():
    if not isinstance(Svg._PLACEHOLDER, Svg):
      placeholder = Svg()
      placeholder.text(Svg._PLACEHOLDER[0], color='MONO')
      placeholder._bounds = (
          -(Svg._PLACEHOLDER[1]+0)*placeholder._bounds[2],
           (Svg._PLACEHOLDER[2]+0)*placeholder._bounds[1],
           (Svg._PLACEHOLDER[1]+1)*placeholder._bounds[2],
          -(Svg._PLACEHOLDER[2]+1)*placeholder._bounds[1])
      Svg._PLACEHOLDER = placeholder
    return Svg._PLACEHOLDER

  def get_viewbox(self):
    if not self._bounds:
      return Svg._get_placeholder().get_viewbox()
    return (
      self._bounds[0],
      self._bounds[1],
      self._bounds[2] - self._bounds[0],
      self._bounds[3] - self._bounds[1]
    )

  def __repr__(self):
    """Returns a string of the SVG"""
    svg = []
    if self.header:
      svg.append('<?xml version="1.0"?>')
      svg.append('<!DOCTYPE svg PUBLIC "-//W3C//DTD SVG 1.1//EN"' +
                 ' "http://www.w3.org/Graphics/SVG/1.1/DTD/svg11.dtd">')
    viewbox = self.get_viewbox()
    svg.append(' '.join(('<svg xmlns="http://www.w3.org/2000/svg"',
               'xmlns:xlink="http://www.w3.org/1999/xlink"',
               'viewBox="%d,%d,%d,%d"' % viewbox,
               'width="%s" height="%s"' % tuple(map(Svg.toinch, viewbox[2:4])),
               'font-family="monospace"',
               'font-size="%f"' % (Svg.FONT_SIZE * self.font_size),
               'stroke-width="%d"' % Svg.THICKNESS['thin'],
               'style="background-color:%s"' % self.bgcolor,
               )) + '>')
    if self._hasAnimation:
      svg += self._autoAnimate
    # Add all symbols
    for name, symsvg in self.symbols.items():
      if symsvg is None:
        continue
      svg.append('<symbol id="%s" overflow="visible">' % name)
      svg += symsvg.data
      svg.append('</symbol>')
    svg += self.data or Svg._get_placeholder().data
    svg.append('</svg>\n')
    return '\n'.join(svg)