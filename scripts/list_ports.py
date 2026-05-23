"""List available serial ports. No Roomba connection required."""

from __future__ import annotations

from serial.tools import list_ports


def main() -> None:
    ports = list(list_ports.comports())
    if not ports:
        print("No serial ports found.")
        return
    print(f"{'device':<14} {'description':<40} hwid")
    for p in ports:
        print(f"{p.device:<14} {p.description:<40} {p.hwid}")


if __name__ == "__main__":
    main()
