import struct

from backend.extractors import extract_mobi


def test_mobi_reads_title_and_exth_metadata(tmp_path):
    path = tmp_path / "book.mobi"
    data = bytearray(1100)
    data[60:68] = b"BOOKMOBI"
    struct.pack_into(">H", data, 76, 2)
    struct.pack_into(">I", data, 78, 128)
    struct.pack_into(">I", data, 86, 1024)
    mobi = 128 + 16
    data[mobi:mobi + 4] = b"MOBI"
    struct.pack_into(">I", data, mobi + 4, 232)
    struct.pack_into(">I", data, mobi + 112, 0x40)

    entries = [(100, b"Fisher, Roger"), (101, b"Campus Verlag"),
               (105, b"Ratgeber"), (524, b"de")]
    exth = mobi + 232
    data[exth:exth + 4] = b"EXTH"
    struct.pack_into(">I", data, exth + 4, 12 + sum(8 + len(value) for _, value in entries))
    struct.pack_into(">I", data, exth + 8, len(entries))
    cursor = exth + 12
    for kind, value in entries:
        struct.pack_into(">II", data, cursor, kind, 8 + len(value))
        data[cursor + 8:cursor + 8 + len(value)] = value
        cursor += 8 + len(value)

    title = "Das Harvard-Konzept".encode()
    struct.pack_into(">I", data, mobi + 68, cursor - 128)
    struct.pack_into(">I", data, mobi + 72, len(title))
    data[cursor:cursor + len(title)] = title
    path.write_bytes(data)

    metadata = extract_mobi(path).metadata
    assert metadata.title.value == "Das Harvard-Konzept"
    assert metadata.authors.value == ["Fisher, Roger"]
    assert metadata.publisher.value == "Campus Verlag"
    assert metadata.language.value == "de"
    assert metadata.genres.value == ["Ratgeber"]
