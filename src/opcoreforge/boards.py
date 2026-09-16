"""
Mac board IDs, and which macOS each one can be asked for.

The board ID is what Apple's recovery servers use to decide which macOS to
send. Getting it wrong does not fail -- it quietly downloads a different
release from the one that was asked for, which is why this table is
transcribed from Dortania's SMBIOS support page rather than written from
memory:

    https://dortania.github.io/OpenCore-Install-Guide/extras/smbios-support.html

The board is chosen by the *macOS wanted*, not by the SMBIOS the EFI presents.
Those are often different: a MacBookPro10,1 is a perfectly good SMBIOS for an
Ivy Bridge laptop, but its board stops at Catalina, so asking Apple for
Sequoia with it gets Catalina. Which releases a board covers comes from
OpCore-Simplify's own model data, so the two halves of the answer each come
from the project that maintains them.
"""

from __future__ import annotations

#: SMBIOS model -> board ID, from the Dortania table above. Where the page
#: lists a model more than once, the first board ID is kept.
BOARD_IDS = {
    "MacBook1,1":     "Mac-F4208CC8",
    "MacBook2,1":     "Mac-F4208CA9",
    "MacBook3,1":     "Mac-F22788C8",
    "MacBook4,1":     "Mac-F22788A9",
    "MacBook5,1":     "Mac-F42D89C8",
    "MacBook5,2":     "Mac-F22788AA",
    "MacBook6,1":     "Mac-F22C8AC8",
    "MacBook7,1":     "Mac-F22C89C8",
    "MacBook8,1":     "Mac-BE0E8AC46FE800CC",
    "MacBook9,1":     "Mac-9AE82516C7C6B903",
    "MacBook10,1":    "Mac-EE2EBD4B90B839A8",
    "MacBookAir1,1":  "Mac-F42C8CC8",
    "MacBookAir2,1":  "Mac-F42D88C8",
    "MacBookAir3,1":  "Mac-942452F5819B1C1B",
    "MacBookAir3,2":  "Mac-942C5DF58193131B",
    "MacBookAir4,1":  "Mac-C08A6BB70A942AC2",
    "MacBookAir4,2":  "Mac-742912EFDBEE19B3",
    "MacBookAir5,1":  "Mac-66F35F19FE2A0D05",
    "MacBookAir5,2":  "Mac-2E6FAB96566FE58C",
    "MacBookAir6,1":  "Mac-35C1E88140C3E6CF",
    "MacBookAir6,2":  "Mac-7DF21CB3ED6977E5",
    "MacBookAir7,1":  "Mac-9F18E312C5C2BF0B",
    "MacBookAir7,2":  "Mac-937CB26E2E02BB01",
    "MacBookAir8,1":  "Mac-827FAC58A8FDFA22",
    "MacBookAir8,2":  "Mac-226CB3C6A851A671",
    "MacBookAir9,1":  "Mac-0CFF9C7C2B63DF8D",
    "MacBookPro1,1":  "Mac-F425BEC8",
    "MacBookPro1,2":  "Mac-F42DBEC8",
    "MacBookPro2,1":  "Mac-F42189C8",
    "MacBookPro2,2":  "Mac-F42187C8",
    "MacBookPro3,1":  "Mac-F4238BC8",
    "MacBookPro4,1":  "Mac-F42C89C8",
    "MacBookPro5,1":  "Mac-F42D86C8",
    "MacBookPro5,2":  "Mac-F2268EC8",
    "MacBookPro5,3":  "Mac-F22587C8",
    "MacBookPro5,4":  "Mac-F22587A1",
    "MacBookPro5,5":  "Mac-F2268AC8",
    "MacBookPro6,1":  "Mac-F22589C8",
    "MacBookPro6,2":  "Mac-F22586C8",
    "MacBookPro7,1":  "Mac-F222BEC8",
    "MacBookPro8,1":  "Mac-94245B3640C91C81",
    "MacBookPro8,2":  "Mac-94245A3940C91C80",
    "MacBookPro8,3":  "Mac-942459F5819B171B",
    "MacBookPro9,1":  "Mac-4B7AC7E43945597E",
    "MacBookPro9,2":  "Mac-6F01561E16C75D06",
    "MacBookPro10,1": "Mac-C3EC7CD22292981F",
    "MacBookPro10,2": "Mac-AFD8A9D944EA4843",
    "MacBookPro11,1": "Mac-189A3D4F975D5FFC",
    "MacBookPro11,2": "Mac-3CBD00234E554E41",
    "MacBookPro11,3": "Mac-2BD1B31983FE1663",
    "MacBookPro11,4": "Mac-06F11FD93F0323C5",
    "MacBookPro11,5": "Mac-06F11F11946D27C5",
    "MacBookPro12,1": "Mac-E43C1C25D4880AD6",
    "MacBookPro13,1": "Mac-473D31EABEB93F9B",
    "MacBookPro13,2": "Mac-66E35819EE2D0D05",
    "MacBookPro13,3": "Mac-A5C67F76ED83108C",
    "MacBookPro14,1": "Mac-B4831CEBD52A0C4C",
    "MacBookPro14,2": "Mac-CAD6701F7CEA0921",
    "MacBookPro14,3": "Mac-551B86E5744E2388",
    "MacBookPro15,1": "Mac-937A206F2EE63C01",
    "MacBookPro15,2": "Mac-827FB448E656EC26",
    "MacBookPro15,3": "Mac-1E7E29AD0135F9BC",
    "MacBookPro15,4": "Mac-53FDB3D8DB8CA971",
    "MacBookPro16,1": "Mac-E1008331FDC96864",
    "MacBookPro16,2": "Mac-5F9802EFE386AA28",
    "MacBookPro16,3": "Mac-E7203C0F68AA0004",
    "MacBookPro16,4": "Mac-A61BADE1FDAD7B05",
    "Macmini1,1":     "Mac-F4208EC8",
    "Macmini2,1":     "Mac-F4208EAA",
    "Macmini3,1":     "Mac-F22C86C8",
    "Macmini4,1":     "Mac-F2208EC8",
    "Macmini5,1":     "Mac-8ED6AF5B48C039E1",
    "Macmini5,2":     "Mac-4BC72D62AD45599E",
    "Macmini5,3":     "Mac-7BA5B2794B2CDB12",
    "Macmini6,1":     "Mac-031AEE4D24BFF0B1",
    "Macmini6,2":     "Mac-F65AE981FFA204ED",
    "Macmini7,1":     "Mac-35C5E08120C7EEAF",
    "Macmini8,1":     "Mac-7BA5B2DFE22DDD8C",
    "iMac4,1":        "Mac-F42786C8",
    "iMac4,2":        "Mac-F4218EC8",
    "iMac5,1":        "Mac-F4228EC8",
    "iMac5,2":        "Mac-F4218EC8",
    "iMac6,1":        "Mac-F4218FC8",
    "iMac7,1":        "Mac-F42386C8",
    "iMac8,1":        "Mac-F227BEC8",
    "iMac9,1":        "Mac-F2218FA9",
    "iMac10,1":       "Mac-F221DCC8",
    "iMac11,1":       "Mac-F2268DAE",
    "iMac11,2":       "Mac-F2268AC8",
    "iMac11,3":       "Mac-F2238BAE",
    "iMac12,1":       "Mac-942B5BF58194151B",
    "iMac12,2":       "Mac-942B59F58194171B",
    "iMac13,1":       "Mac-00BE6ED71E35EB86",
    "iMac13,2":       "Mac-FC02E91DDD3FA6A4",
    "iMac13,3":       "Mac-7DF2A3B5E5D671ED",
    "iMac14,1":       "Mac-031B6874CF7F642A",
    "iMac14,2":       "Mac-27ADBB7B4CEE8E61",
    "iMac14,3":       "Mac-77EB7D7DAF985301",
    "iMac14,4":       "Mac-81E3E92DD6088272",
    "iMac15,1":       "Mac-42FD25EABCABB274",
    "iMac16,1":       "Mac-A369DDC4E67F1C45",
    "iMac16,2":       "Mac-FFE5EF870D7BA81A",
    "iMac17,1":       "Mac-DB15BD556843C820",
    "iMac18,1":       "Mac-4B682C642B45593E",
    "iMac18,2":       "Mac-77F17D7DA9285301",
    "iMac18,3":       "Mac-BE088AF8C5EB4FA2",
    "iMac19,1":       "Mac-AA95B1DDAB278B95",
    "iMac19,2":       "Mac-63001698E7A34814",
    "iMac20,1":       "Mac-CFF7D910A743CAAF",
    "iMac20,2":       "Mac-AF89B6D9451A490B",
    "iMacPro1,1":     "Mac-7BA5B2D9E42DDD94",
    "MacPro1,1":      "Mac-F4208DC8",
    "MacPro2,1":      "Mac-F4208DA9",
    "MacPro3,1":      "Mac-F42C88C8",
    "MacPro4,1":      "Mac-F221BEC8",
    "MacPro5,1":      "Mac-F221BEC8",
    "MacPro6,1":      "Mac-F60DEB81FF30ACF6",
    "MacPro7,1":      "Mac-27AD2F918AE68F61",
    "Xserve1,1":      "Mac-F4208AC8",
    "Xserve2,1":      "Mac-F42289C8",
    "Xserve3,1":      "Mac-F223BEC8",
}


def board_for(model: str):
    """The board ID for an SMBIOS model, or None if it is not listed."""
    return BOARD_IDS.get((model or "").strip())


def options_for(darwin_version: str, os_data=None, mac_devices=None) -> list:
    """Boards that can be asked for *darwin_version*, newest model first.

    Returns ``(board_id, model, label)``. The support range comes from
    OpCore-Simplify's ``mac_model_data``; a model with no board ID here, or no
    entry there, is simply not offered rather than guessed at.
    """
    if mac_devices is None:
        from ocs_scripts.datasets.mac_model_data import mac_devices
    if os_data is None:
        from ocs_scripts.datasets import os_data

    def parse(value):
        try:
            return tuple(int(part) for part in str(value).split(".")[:3])
        except Exception:
            return (0, 0, 0)

    wanted = parse(darwin_version)
    if wanted == (0, 0, 0):
        return []

    out = []
    for device in mac_devices:
        board = BOARD_IDS.get(device.name)
        if not board:
            continue
        if not (parse(device.initial_support) <= wanted
                <= parse(device.last_supported_version)):
            continue
        newest = os_data.get_macos_name_by_darwin(
            device.last_supported_version) or ""
        out.append((board, device.name,
                    "%s  -  up to %s" % (device.name, newest)))

    def sort_key(item):
        model = item[1]
        family = "".join(c for c in model if not c.isdigit() and c != ",")
        numbers = [int(n) for n in
                   "".join(c if c.isdigit() else " " for c in model).split()]
        return (family, [-n for n in numbers])

    out.sort(key=sort_key)
    return out


def preferred(options, smbios_model: str = ""):
    """Which option to offer first.

    The SMBIOS the EFI presents, when that board reaches the release wanted --
    it is the closest thing to "this machine". Otherwise the first listed,
    which is the newest model of the first family.
    """
    if not options:
        return None
    for board, model, _label in options:
        if model == (smbios_model or "").strip():
            return board
    return options[0][0]
