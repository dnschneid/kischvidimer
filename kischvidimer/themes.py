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

def themes():
  return (
      ("Default colors", default),
      ("Classic colors", classic),
      ("Black & white", blacknwhite),
      )

def get(theme=None):
  colormap = {
      # Keyword -> default color mapping
      # Reference: builtin_color_themes.h
      # Reference: layer_id.cpp (SCH_LAYER_ID)
      # Reference: 42 items in Preferences
      # Renderables
      "wire":                   'var(--w)',  # Wires
      "bus":                    'var(--W)',  # Buses
      "bus_junction":           'var(--J)',  # Bus junctions
      "junction":               'var(--j)',  # Junctions
      "loclabel":               'var(--l)',  # Labels
      "globlabel":              'var(--L)',  # Global labels
      "hierlabel":              'var(--h)',  # Hierarchical labels
      "pinnum":                 'var(--n)',  # Pin numbers
      "pinnam":                 'var(--N)',  # Pin names
      "referencepart":          'var(--r)',  # Symbol references
      "valuepart":              'var(--v)',  # Symbol values
      "fields":                 'var(--f)',  # Symbol fields
      "intersheet_refs":        'var(--q)',  # Sheet references
      "netclass_refs":          'var(--Q)',  # Net class references
      "device":                 'var(--b)',  # Symbol body outlines
      "device_background":      'var(--B)',  # Symbol body fills
      "notes":                  'var(--g)',  # Schematic text && graphics
      "private_notes":          'var(--gp)', # Symbol private text && graphics
      "notes_background":       'var(--G)',  # Schematic text && graphics backgrounds
      "pin":                    'var(--p)',  # Pins
      "sheet":                  'var(--s)',  # Sheet borders
      "sheet_background":       'var(--S)',  # Sheet backgrounds
      "sheetname":              'var(--a)',  # Sheet names
      "sheetfields":            'var(--F)',  # Sheet fields
      "sheetfilename":          'var(--A)',  # Sheet file names
      "sheetlabel":             'var(--P)',  # Sheet pins
      "noconnect":              'var(--x)',  # No-connect symbols
      "dnp_marker":             'var(--X)',  # DNP markers
      "schematic_background":   'var(--d)',  # Background
      "schematic_drawingsheet": 'var(--D)',  # Drawing sheet
      # UI
      "hovered":                'var(--O)',  # Hovered items
      "brightened":             'var(--H)',  # Highlighted items
      #"ERC_WARN":               'var(-- )',  # ERC warnings
      #"ERC_ERR":                'var(-- )',  # ERC errors
      #"ERC_EXCLUSION":          'var(-- )',  # ERC exclusions
      #"SCHEMATIC_ANCHOR":       'var(-- )',  # Anchors
      #"SCHEMATIC_AUX_ITEMS":    'var(-- )',  # Helper items
      #"SCHEMATIC_GRID":         'var(-- )',  # Grid
      #"SCHEMATIC_GRID_AXES":    'var(-- )',  # Axes
      #"SCHEMATIC_CURSOR":       'var(-- )',  # Cursor
      #"HIDDEN":                 'var(-- )',  # Hidden items
      #"SELECTION_SHADOWS":      'var(-- )',  # Selection highlight
      #"SCHEMATIC_PAGE_LIMITS":  'var(-- )',  # Page limits
      #"OP_VOLTAGES":            'var(-- )',  # Operating point voltages
      #"OP_CURRENTS":            'var(-- )',  # Operating point currents

      # Quirk mappings
      # INTERSHEET_REFS doesn't appear to exist in the theme
      # it just uses globlabel. Other label fields use "fields"
      "intersheet_refs":        'globlabel',
      # For some reason NOTES_BACKGROUND is zero. KiCad uses NOTES instead
      "notes_background":       'notes',

      # Theme mappings
      # The names in plugin themes are different from internal names
      "anchor":            "SCHEMATIC_ANCHOR",
      "aux_items":         "SCHEMATIC_AUX_ITEMS",
      "background":        "SCHEMATIC_BACKGROUND",
      "component_body":    "DEVICE_BACKGROUND",
      "component_outline": "DEVICE",
      "cursor":            "SCHEMATIC_CURSOR",
      "erc_error":         "ERC_ERR",
      "erc_warning":       "ERC_WARN",
      "grid":              "SCHEMATIC_GRID",
      "grid_axes":         "SCHEMATIC_GRID_AXES",
      "label_global":      "GLOBLABEL",
      "label_hier":        "HIERLABEL",
      "label_local":       "LOCLABEL",
      "netclass_flag":     "NETCLASS_REFS",
      "rule_area":         "RULE_AREAS",
      "no_connect":        "NOCONNECT",
      "note":              "NOTES",
      "private_note":      "PRIVATE_NOTES",
      "note_background":   "NOTES_BACKGROUND",
      "pin_name":          "PINNAM",
      "pin_number":        "PINNUM",
      "reference":         "REFERENCEPART",
      "shadow":            "SELECTION_SHADOWS",
      "sheet_filename":    "SHEETFILENAME",
      "sheet_fields":      "SHEETFIELDS",
      "sheet_label":       "SHEETLABEL",
      "sheet_name":        "SHEETNAME",
      "value":             "VALUEPART",
      "worksheet":         "SCHEMATIC_DRAWINGSHEET",
  }
  if theme:
    if isinstance(theme, str):
      theme = globals()[theme]
    for name, color in dict(theme).items():
      name = name.lower()
      if name in colormap:
        colormap[colormap[name]] = color
  return colormap

default = (
    ( 'SCHEMATIC_ANCHOR',       (   0,   0, 255) ),
    ( 'SCHEMATIC_AUX_ITEMS',    (   0,   0,   0) ),
    ( 'SCHEMATIC_BACKGROUND',   ( 245, 244, 239) ),
    ( 'HOVERED',                (   0,   0, 255) ),
    ( 'BRIGHTENED',             ( 255,   0, 255) ),
    ( 'BUS',                    (   0,   0, 132) ),
    ( 'BUS_JUNCTION',           (   0,   0, 132) ),
    ( 'DEVICE_BACKGROUND',      ( 255, 255, 194) ),
    ( 'DEVICE',                 ( 132,   0,   0) ),
    ( 'SCHEMATIC_CURSOR',       (  15,  15,  15) ),
    ( 'DNP_MARKER',             ( 220,   9,  13, 0.7 ) ),
    ( 'ERC_ERR',                ( 230,   9,  13, 0.8 ) ),
    ( 'ERC_WARN',               ( 209, 146,   0, 0.8 ) ),
    ( 'ERC_EXCLUSION',          ( 94,  194, 194, 0.8 ) ),
    ( 'FIELDS',                 ( 132,   0, 132 ) ),
    ( 'SCHEMATIC_GRID',         ( 181, 181, 181 ) ),
    ( 'SCHEMATIC_GRID_AXES',    (   0,   0, 132 ) ),
    ( 'HIDDEN',                 (  94, 194, 194 ) ),
    ( 'JUNCTION',               (   0, 150,   0 ) ),
    ( 'GLOBLABEL',              ( 132,   0,   0 ) ),
    ( 'HIERLABEL',              ( 114,  86,   0 ) ),
    ( 'LOCLABEL',               (  15,  15,  15 ) ),
    ( 'NETCLASS_REFS',          (  72,  72,  72 ) ),
    ( 'NOCONNECT',              (   0,   0, 132 ) ),
    ( 'NOTES',                  (   0,   0, 194 ) ),
    ( 'PRIVATE_NOTES',          (  72,  72, 255 ) ),
    ( 'PIN',                    ( 132,   0,   0 ) ),
    ( 'PINNAM',                 (   0, 100, 100 ) ),
    ( 'PINNUM',                 ( 169,   0,   0 ) ),
    ( 'REFERENCEPART',          (   0, 100, 100 ) ),
    ( 'SHEET',                  ( 132,   0,   0 ) ),
    ( 'SHEET_BACKGROUND',       ( 255, 255, 255,   0 ) ),
    ( 'SHEETFILENAME',          ( 114,  86,   0 ) ),
    ( 'SHEETFIELDS',            ( 132,   0, 132 ) ),
    ( 'SHEETLABEL',             (   0, 100, 100 ) ),
    ( 'SHEETNAME',              (   0, 100, 100 ) ),
    ( 'VALUEPART',              (   0, 100, 100 ) ),
    ( 'WIRE',                   (   0, 150,   0 ) ),
    ( 'SCHEMATIC_DRAWINGSHEET', ( 132,   0,   0 ) ),
    ( 'SCHEMATIC_PAGE_LIMITS',  ( 181, 181, 181 ) ),
    ( 'OP_VOLTAGES',            ( 132,   0,  50 ) ),
    ( 'OP_CURRENTS',            ( 224,   0,  12 ) ),
    )

classic = (
    ( 'SCHEMATIC_ANCHOR',       (  0,   0, 255, 1 ) ),
    ( 'SCHEMATIC_AUX_ITEMS',    (  0,   0,   0) ),
    ( 'SCHEMATIC_BACKGROUND',   (255, 255, 255) ),
    ( 'HOVERED',                (  0,   0, 132) ),
    ( 'BRIGHTENED',             (255,   0, 255) ),
    ( 'BUS',                    (  0,   0, 132) ),
    ( 'BUS_JUNCTION',           (  0,   0, 132) ),
    ( 'DEVICE_BACKGROUND',      (255, 255, 194) ),
    ( 'DEVICE',                 (132,   0,   0) ),
    ( 'SCHEMATIC_CURSOR',       (  0,   0,   0) ),
    ( 'DNP_MARKER',             (255,   0,   0, 0.7 ) ),
    ( 'ERC_ERR',                (255,   0,   0, 0.8 ) ),
    ( 'ERC_WARN',               (  0, 255,   0, 0.8 ) ),
    ( 'ERC_EXCLUSION',          (194, 194, 194) ),
    ( 'FIELDS',                 (132,   0, 132) ),
    ( 'SCHEMATIC_GRID',         (132, 132, 132) ),
    ( 'SCHEMATIC_GRID_AXES',    (  0,   0, 132) ),
    ( 'HIDDEN',                 (194, 194, 194) ),
    ( 'JUNCTION',               (  0, 132,   0) ),
    ( 'GLOBLABEL',              (132,   0,   0) ),
    ( 'HIERLABEL',              (132, 132,   0) ),
    ( 'LOCLABEL',               (  0,   0,   0) ),
    ( 'NETCLASS_REFS',          (  0,   0,   0) ),
    ( 'NOCONNECT',              (  0,   0, 132) ),
    ( 'NOTES',                  (  0,   0, 194) ),
    ( 'PRIVATE_NOTES',          (  0,   0, 194) ),
    ( 'PIN',                    (132,   0,   0) ),
    ( 'PINNAM',                 (  0, 132, 132) ),
    ( 'PINNUM',                 (132,   0,   0) ),
    ( 'REFERENCEPART',          (  0, 132, 132) ),
    ( 'SELECTION_SHADOWS',      (255, 179, 102, 0.8 ) ),
    ( 'SHEET',                  (132,   0, 132) ),
    ( 'SHEET_BACKGROUND',       (255, 255, 255, 0.0 ) ),
    ( 'SHEETFILENAME',          (132, 132,   0) ),
    ( 'SHEETFIELDS',            (132,   0, 132) ),
    ( 'SHEETLABEL',             (  0, 132, 132) ),
    ( 'SHEETNAME',              (  0, 132, 132) ),
    ( 'VALUEPART',              (  0, 132, 132) ),
    ( 'WIRE',                   (  0, 132,   0) ),
    ( 'SCHEMATIC_DRAWINGSHEET', (132,   0,   0) ),
    ( 'OP_VOLTAGES',            ( 72,   0,  72) ),
    ( 'OP_CURRENTS',            (132,   0,   0) ),
    )

blacknwhite = tuple(
    (name,
      (255,255,255) if 'BACKGROUND' in name else
      (0,0,0,color[-1]) if 0 < color[-1] < 1 else
      (0,0,0)
    ) for name, color in default)

def todict():
  colormap = get()
  themedict = {}
  for i, (themename, theme) in enumerate(themes()):
    # Always extend the default theme
    themedict[themename] = d = dict(themedict.get(themes()[0][0], {}))
    for name, color in dict(theme).items():
      name = name.lower()
      while name in colormap and not colormap[name].startswith('var('):
        name = colormap[name].lower()
      if name in colormap:
        var = colormap[name].rpartition("-")[2].rstrip(")")
        while color in colormap:
          color = colormap[color]
        if isinstance(color, tuple):
          color = f"rgb{'a'*(len(color)==4)}({','.join(map(str,color))})"
        d[var] = color
  numitems = 0
  for d in themedict.values():
    numitems = numitems or len(d)
    assert numitems == len(d)
  return themedict
