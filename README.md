<!--
SPDX-FileCopyrightText: (C) 2025 Rivos Inc.
SPDX-License-Identifier: Apache-2.0
-->

# kischvidimer

The KiCad schematic viewer / differ / merger.

(although right now it just views)

## tl;dr

kischvidimer generates a self-contained HTML webapp with your KiCad schematic
embedded. You can view the file locally, send it as an email attachment, or host
it on a webserver.

Give it a test whirl:

```
python3 -m kischvidimer schgen .../your_project.kicad_pro -o your_project.html
```

Or install it:

```
pip3 install .
cd .../your_project
kischvidimer schgen your_project.kicad_pro -o your_project.html
```

## What about layout?

Nope. Try [KiCanvas](https://kicanvas.org/) instead?

## OK then why does this exist?

Think of it as a PDF export of your schematic, but way, way better. kischvidimer
is designed to scale well to handle absolutely ginormous schematics, complex
variable usage, and large amounts of hierarchy and reuse.

As an example, exporting a real-life 158-page schematic as a PDF results in a
42MiB file. The HTML file generated by kischvidimer is 1.8MiB (<5%!), includes a
bunch of metadata and navigational aids, and can be viewed with ease on your
phone. You can even hit the print button and generate a massive PDF if you so
desire.

The HTML file has zero external dependencies and will not attempt to communicate
with any server (unless you launch a datasheet, of course), so you really can
treat it just like a PDF.

## Why does it look so ugly?

Glad you asked 🥲

Currently kischvidimer does not have any embedded fonts, so the default KiCad
font has to be approximated with...Arial.

### KiCad has SVG output!

It does! And the output is visually perfect! But the files are large, have hacks
of their own around text glyphs, and are difficult to inject metadata into. It
would be nigh impossible to implement the diffing and merging UI with them.

## What's in the works?

 * KiCad 9 support
 * Netlist intelligence and navigation
 * Diffing and merging, of course

## How to pronounce kischvidimer?

Well, KiCad is pronounced KEY-cad, so I guess it should be KEY-schvidimer.
