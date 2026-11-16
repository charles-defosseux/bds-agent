"""IBAN checksum (ISO 13616, mod 97). All IBANs in this project are fictional but must be valid."""
import re

_SHAPE = re.compile(r"^[A-Z]{2}\d{2}[A-Z0-9]{11,30}$")


def is_valid_iban(iban: str) -> bool:
    iban = iban.replace(" ", "").upper()
    if not _SHAPE.match(iban):
        return False
    rearranged = iban[4:] + iban[:4]
    digits = "".join(str(int(c, 36)) for c in rearranged)
    return int(digits) % 97 == 1
