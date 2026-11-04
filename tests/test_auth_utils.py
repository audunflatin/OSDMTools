"""Passordhashing: kompatibilitet med hasher laget av passlib før november 2026."""
from backend.auth_utils import hash_password, verify_password, needs_rehash

# Laget med passlib 1.7.4 (CryptContext(schemes=["pbkdf2_sha256"])) før byttet.
PASSLIB_HASH = "$pbkdf2-sha256$29000$2Nt7z1nrXct5r9Wac26N0Q$5VfPPih43wHnI2RW2cRNCnFXRKZ.Z6KjBsEOA3ReeeU"
PASSLIB_PASSWORD = "Gammelt-passord-æøå1"


def test_verifies_existing_passlib_hash():
    assert verify_password(PASSLIB_PASSWORD, PASSLIB_HASH)
    assert not verify_password("feil", PASSLIB_HASH)


def test_old_hash_needs_rehash_new_does_not():
    assert needs_rehash(PASSLIB_HASH)
    assert not needs_rehash(hash_password("x", rounds=600_000))


def test_roundtrip_and_salt():
    h1, h2 = hash_password("hemmelig", rounds=1000), hash_password("hemmelig", rounds=1000)
    assert h1 != h2  # unikt salt
    assert verify_password("hemmelig", h1) and verify_password("hemmelig", h2)
    assert not verify_password("Hemmelig", h1)


def test_garbage_hash_is_rejected():
    for bad in ("", "abc", "$pbkdf2-sha256$x$y$z", "$bcrypt$..."):
        assert not verify_password("x", bad)
