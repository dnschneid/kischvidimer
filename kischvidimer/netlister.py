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

# This module generates a netlist of a design, attempting to match KiCad's
# behaviors as closely as possible. There are, however a bunch of edge cases
# around implicitly-named nets that are not yet perfectly duplicated here:
#  * A member net of a bus, where the bus is only named at a deeper level of
#    hierarchy should ignore the higher-level net label when determining the net
#    final name. Currently, this netlister prioritizes the higher-level label.
#  * A net that is connected between two blocks and not named at the higher
#    level should not simply pick the lexicographically-minimum name. There is
#    some other criteria that KiCad uses that needs to be investigated.
#  * Unnamed nets should not have the pin number if the pin name is unique
#    within the part. Single-node nets always include the pin number, though.
#  * Unnamed nets prioritize being auto-named by pins with pin names.
#  * Alternates are not considered

import re
from collections import namedtuple

from .kicad_common import HasUUID, unit_to_alpha
from .kicad_sym import SymbolBody


class NetBus:
  CAT_NETTIE = 0
  CAT_POWER = 1
  CAT_LABEL = 2
  CAT_SYMPIN = 3
  CAT_SYMPIN_PWR = 4
  CAT_SHEETPIN = 5
  CAT_NC = 6

  @staticmethod
  def new(is_bus):
    return Bus() if is_bus else Net()

  def __init__(self):
    self._mergedinto = None
    self._names = set()
    self._ncs = set()

  def merge_into(self, item):
    assert self._mergedinto is None
    while item._mergedinto is not None:
      item = item._mergedinto
    if item is not self:
      item._names.update(self._names)
      item._ncs.update(self._ncs)
      self._mergedinto = item
    return item

  def add_name(self, name, category):
    assert self._mergedinto is None
    if category == NetBus.CAT_SYMPIN and name[0].startswith("#"):
      category = NetBus.CAT_SYMPIN_PWR
    depth = name.count("/")
    sortname = (
      name.upper() if isinstance(name, str) else tuple(n.upper() for n in name)
    )
    self._names.add((category, depth, sortname, name))

  def name(self):
    while self._mergedinto:
      self = self._mergedinto
    if not self._names:
      return None
    name = min(self._names)
    if name[0] == NetBus.CAT_SYMPIN:
      pre = (
        "Net"
        if sum(1 for n in self._names if n[0] == NetBus.CAT_SYMPIN) > 1
        else "unconnected"
      )
      pinname = list(name[-1])
      pinname[-1] = f"Pad{pinname[-1]}"
      pinname = "-".join(p for p in pinname if p and p != "~")
      return f"{pre}-({pinname})"
    return name[-1]

  def add_nc(self, instance):
    self._ncs.add(instance)

  def __eq__(self, other):
    while other._mergedinto is not None:
      other = other._mergedinto
    while self._mergedinto is not None:
      self = self._mergedinto
    return self is other


class Net(NetBus):
  FMT_SHORT = 0  # net: U1.1 U2.1
  FMT_NAMES = 1  # net: U1.1(VDD) U2.1(VOUT)
  FMT_TELESIS = 2  # 'NET';,\n\tU1.1,\n\tU2.1
  REMOVE_UNIT_RE = re.compile("[A-Z]+$")
  TEL_QUOTE_RE = re.compile("[^a-zA-Z0-9_/]")

  def _get_pins(self):
    return {
      (Net.REMOVE_UNIT_RE.sub("", n[0]),) + n[1:]
      for c, _, _, n in self._names
      if c == NetBus.CAT_SYMPIN
    }

  def fmt(self, fmt):
    name = self.name()
    if name is None:
      return ""
    pins = sorted(self._get_pins(), key=lambda n: (n[0], n[2], n[1]))
    if not pins:
      return ""
    # Drop explicitly NC'd nets
    if len(pins) == 1 and self._ncs:
      return ""
    if fmt == Net.FMT_SHORT:
      nodes = " ".join(f"{r}.{num}" for r, _, num in pins if r[0] != "#")
      return f"{name}: {nodes}"
    if fmt == Net.FMT_NAMES:
      nodes = " ".join(
        f"{r}.{num}" + f"({name})" * ((name or "~") not in (num, "~"))
        for r, name, num in pins
        if r[0] != "#"
      )
      return f"{name}: {nodes}"
    if fmt == Net.FMT_TELESIS:
      if Net.TEL_QUOTE_RE.search(name):
        name = f"'{name}'"
      nodes = ",\n\t".join(f"{r}.{num}" for r, _, num in pins if r[0] != "#")
      return f"{name.upper()};,\n\t{nodes}"
    return ""


class Bus(NetBus):
  def __init__(self):
    self.members = ReplaceableDict()
    self._sheetpins = []
    self._subsheet_buses = []
    super().__init__()

  def add_member(self, member, net):
    assert self._mergedinto is None
    assert isinstance(net, Net)
    assert net._mergedinto is None
    return self.members.setrep(member, net)

  def add_sheetpin(self, subsheet_bus, pinname, local_labels):
    assert self._mergedinto is None
    self._subsheet_buses.append(subsheet_bus)
    self._sheetpins.append((pinname, local_labels))
    return self

  def merge_into(self, item):
    assert isinstance(item, Bus)
    assert self._mergedinto is None
    while item._mergedinto is not None:
      item = item._mergedinto
    if item is self:
      return item
    for member in self.members:
      item.add_member(member, self.members.getrep(member))
    item._sheetpins.extend(self._sheetpins)
    item._subsheet_buses.extend(self._subsheet_buses)
    return super().merge_into(item)

  def all_members(self):
    while self._mergedinto is not None:
      self = self._mergedinto
    assert not self._sheetpins  # invalid until sheetpins have been resolved
    return {n.name() for n in self.nets}

  def gen_local_labels(self):
    while self._mergedinto is not None:
      self = self._mergedinto
    sheetpins = self._sheetpins
    self._sheetpins = []
    # Generate local labels only if there was no label
    if not sheetpins or self.name() is not None:
      return []
    assert not self.members
    il_members = min(sheetpins, key=lambda sp: (sp[0].upper(), sp[0]))[-1]
    labels = [(il, self.add_member(member, Net())) for il, member in il_members]
    return labels

  def resolve_sheetpins(self):
    while self._mergedinto is not None:
      self = self._mergedinto
    assert not self._sheetpins  # gen_local_labels should have been done first
    subsheet_buses = self._subsheet_buses  # pre-clear so we can merge
    self._subsheet_buses = []
    for subsheet_bus in subsheet_buses:
      while subsheet_bus._mergedinto is not None:
        subsheet_bus = subsheet_bus._mergedinto
      # Only merge the whole bus if the members are the exact same
      if subsheet_bus.members.keys() == self.members.keys():
        subsheet_bus.merge_into(self)
        continue
      # Otherwise, merge the members that overlap
      for member in self.members:
        if member in subsheet_bus.members:
          subsheet_bus.add_member(member, self.members.getrep(member))


class ReplaceableDict(dict):
  def getrep(self, key, setdefault=None):
    item = self.get(key)
    if item is None:
      if setdefault is None:
        raise KeyError(key)
      self[key] = setdefault
      return setdefault
    while item._mergedinto is not None:
      self[key] = item = item._mergedinto
    return item

  def setrep(self, key, item):
    assert item._mergedinto is None
    curitem = self.getrep(key, item)
    self[key] = curitem.merge_into(item)
    return item


class Instance(namedtuple("Instance", ["instance"])):
  def __new__(cls, context, include_uuid=False):
    if isinstance(context, Instance):
      return super().__new__(cls, instance=context.instance)
    path = None
    sheet = None
    for c in reversed(context):
      if c.type == "path":
        path = c
      elif c.type == "sheet":
        sheet = c
    instance = path.uuid(sheet)
    if include_uuid:
      # Include the last context item's UUID in the path
      for c in reversed(context):
        if isinstance(c, HasUUID):
          instance = f"{instance.rstrip('/')}/{c.uuid(True)}"
          break
    return super().__new__(cls, instance=instance)


class InstCoord(namedtuple("InstCoord", ["instance", "x", "y", "is_bus"])):
  def __new__(cls, context, item, is_bus):
    inst = Instance(context)
    # FIXME: probably no diffs
    x, y = item if isinstance(item, tuple) else item.pts([])[0]
    return super().__new__(cls, instance=inst, x=x, y=y, is_bus=is_bus)


class InstLabel(namedtuple("InstLabel", ["instance", "text"])):
  def __new__(cls, context, item, is_global, include_uuid=False):
    inst = None if is_global else Instance(context, include_uuid=include_uuid)
    # FIXME: probably no diffs
    text = item if isinstance(item, str) else item.net([], context)
    return super().__new__(cls, instance=inst, text=text)


class NetObj(namedtuple("NetObj", ["xys", "is_bus"])):
  """Objects used for collision detection"""

  UNKNOWN = -1

  def __new__(cls, obj, is_bus=UNKNOWN):
    return super().__new__(cls, xys=obj.pts([]), is_bus=is_bus)

  def test(self, other):
    if len(other.xys) > len(self.xys):
      return other.test(self)
    if self.is_bus != other.is_bus and NetObj.UNKNOWN not in (
      self.is_bus,
      other.is_bus,
    ):
      return False
    p = self.xys
    # For line-line detections, only do endpoint comparisons
    if len(other.xys) > 1:
      return any(xy in p for xy in other.xys)
    # Line-point detection
    xy = other.xys[0]
    # Box test
    if not (p[0][0] <= xy[0] <= p[1][0] or p[1][0] <= xy[0] <= p[0][0]):
      return False
    if not (p[0][1] <= xy[1] <= p[1][1] or p[1][1] <= xy[1] <= p[0][1]):
      return False
    # Collinear test
    return (p[1][0] - p[0][0]) * (xy[1] - p[0][1]) == (xy[0] - p[0][0]) * (
      p[1][1] - p[0][1]
    )


class Netlister:
  def __init__(self):
    self._by_instcoord = ReplaceableDict()
    self._by_instlabel = ReplaceableDict()
    self._unresolved_buses = []
    self._nodes_by_inst = {}
    self._wires_by_inst = {}
    self.netprefix = "/"  # updated by callers to set the local net name prefix

  # def get_net(self, context, item):
  #  self.resolve()
  #  for is_bus
  #  ic = InstCoord(context, item, is_bus)

  def _add_node(self, ic, item):
    if ic in self._by_instcoord:
      return self._by_instcoord.getrep(ic)
    wires = self._wires_by_inst.setdefault(ic.instance, [])
    node = NetObj(item, ic.is_bus)
    netbus = None
    for wire in wires:
      if wire.test(node):
        netbus = self._by_instcoord.getrep(
          InstCoord(ic.instance, wire.xys[0], ic.is_bus)
        )
        break
    else:
      netbus = NetBus.new(ic.is_bus)
    self._by_instcoord[ic] = netbus
    return netbus

  def _add_wire(self, inst, wire):
    self._wires_by_inst.setdefault(inst, []).append(wire)
    nodes = self._nodes_by_inst.setdefault(inst, [])
    netbus = None
    for i in range(len(nodes) - 1, -1, -1):
      node = nodes[i]
      if wire.test(node):
        ic = InstCoord(inst, node.xys[0], node.is_bus)
        if netbus is None:
          netbus = self._by_instcoord.getrep(ic)
        else:
          netbus = self._by_instcoord.setrep(ic, netbus)
        del nodes[i : i + 1]
    return netbus

  def add_label(self, context, label):
    bus = label.bus([], context)
    is_global = label.type == "global_label"
    ic = InstCoord(context, label, bool(bus))
    il = InstLabel(context, label, is_global)
    netbus = self._add_node(ic, label)
    netbus = self._by_instlabel.setrep(il, netbus)
    netbus.add_name(
      label[0] if is_global else f"{self.netprefix.rstrip('/')}/{label[0]}",
      category=NetBus.CAT_LABEL,
    )
    if not bus:
      return netbus
    for _prefix, member, netname in label.expand_bus([], context):
      il = InstLabel(context, netname, is_global)
      busnet = self._by_instlabel.getrep(il, Net())
      busnet = netbus.add_member(member, busnet)
      busnet.add_name(
        member if is_global else f"{self.netprefix.rstrip('/')}/{netname}",
        category=NetBus.CAT_LABEL,
      )
    return netbus

  def add_sheetpin(self, context, pin):
    bus = pin.bus([], context)
    ic = InstCoord(context, pin, bool(bus))
    # Sheetpins do not cause wire breaks, so need to use _add_node
    netbus = self._add_node(ic, pin)
    # Sheet pins don't generate a netname; they just inherit the local name
    # connect net to subsheet via _by_instlabel
    il = InstLabel(context, pin, False, include_uuid=True)
    if bus:
      # don't immediately connect buses to subsheet
      self._unresolved_buses.append(netbus)
      subsheet_bus = self._by_instlabel.getrep(il, Bus())
      pinname = pin.net([], context)
      local_labels = [
        (InstLabel(context, netname, False), member)
        for _, member, netname in pin.expand_bus([], context)
      ]
      return netbus.add_sheetpin(subsheet_bus, pinname, local_labels)
    return self._by_instlabel.setrep(il, netbus)

  def add_sympin(self, context, pin):
    ref = None
    show_unit = None
    unit = ""
    for c in reversed(context):
      if isinstance(c, SymbolBody):
        unit = unit_to_alpha(c.unit)
      elif hasattr(c, "refdes"):
        ref = c.refdes([], context)
        show_unit = c.show_unit([], context)
        break
    name, number = pin.name_num(context, [])
    pintype = pin.get_type_style(context, [])[0]
    # FIXME: alternates can cause electrical type of hidden pin to be power
    # input (or not). does that still make it a power net?
    is_pwr = pin.hide([]) and pintype == "power_in"
    is_nc = pintype == "no_connect"
    # If name is not empty, include unit letter if there's more than one
    if ref and show_unit and name and name != "~":
      ref += unit
    pinnet = name if is_pwr else (ref, name, number)
    netbus = Net()
    # Pins cause wire breaks in the editor, so can do point checks
    ic = InstCoord(context, pin.pts([], context)[0], False)
    if is_pwr:
      il = InstLabel(context, pinnet, True)
      netbus = self._by_instlabel.getrep(il, netbus)
    elif is_nc:
      netbus.add_nc(ic.instance)
    netbus = self._by_instcoord.setrep(ic, netbus)
    netbus.add_name(
      pinnet, category=NetBus.CAT_POWER if is_pwr else NetBus.CAT_SYMPIN
    )
    return netbus

  def add_nc(self, context, nc):
    # NCs do not contribute any potential net name, so just return the net
    # We also don't know if they're a bus, so return both.
    # Also, NCs do not split lines, so need to use the more complex detection.
    nbs = []
    for is_bus in False, True:
      ic = InstCoord(context, nc, is_bus)
      netbus = self._add_node(ic, nc)
      netbus.add_nc(ic.instance)
      nbs.append(netbus)
    return nbs

  def add_wire(self, context, wire):
    is_bus = wire.type == "bus"
    ic = Instance(context)
    netbus = self._add_wire(ic, NetObj(wire, is_bus))
    for xy in wire.pts([]):
      ic = InstCoord(context, xy, is_bus)
      if netbus is None:
        netbus = self._by_instcoord.getrep(ic, NetBus.new(is_bus))
      else:
        netbus = self._by_instcoord.setrep(ic, netbus)
    return netbus

  def add_busentry(self, context, busentry):
    nbs = []
    pts = busentry.pts([])
    ic = None
    for is_bus in False, True:
      for pt in pts:
        ic = InstCoord(context, pt, is_bus)
        nbs.append(self._by_instcoord.getrep(ic, NetBus.new(is_bus)))
    return nbs

  def add_junction(self, context, junction):
    # junctions shouldn't be able to modify the netlist, since associated wires
    # always terminate at the same coordinate.
    # Returns both a net and a bus, since if the junction is processed first,
    # it'll be unknown which it is
    nbs = []
    for is_bus in False, True:
      ic = InstCoord(context, junction, is_bus)
      nbs.append(self._by_instcoord.getrep(ic, NetBus.new(is_bus)))
    return nbs

  def resolve(self):
    # Processes accumulated netlisting tasks. Call after adding everything.
    for bus in self._unresolved_buses:
      for il, net in bus.gen_local_labels():
        self._by_instlabel.setrep(il, net)
    for bus in self._unresolved_buses:
      bus.resolve_sheetpins()
    self._unresolved_buses.clear()

  def generate_netlist(self, fmt):
    prefix = "$NETS\n" if fmt == Net.FMT_TELESIS else ""
    nets = {
      id(n): n
      for n in self._by_instcoord.values()
      if not n._mergedinto and isinstance(n, Net)
    }
    netlist = []
    for net in nets.values():
      formatted = net.fmt(fmt)
      if formatted:
        netlist.append(formatted)
    return prefix + "\n".join(sorted(netlist, key=lambda x: x.lstrip("'")))

  def __str__(self):
    return self.generate_netlist(Net.FMT_TELESIS)
