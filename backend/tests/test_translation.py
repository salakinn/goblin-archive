import json
import time
import zipfile
from threading import Event
from pathlib import Path
from xml.etree import ElementTree as ET
import pytest
from sqlalchemy import event, select

from backend.ai import AIResult
from backend.covers import CoverAsset, CoverService
from backend.imports import ImportManager, sha256_file
from backend.models import Book
from backend.providers import ProviderChain
from backend.tests.conftest import add_book
from backend.translation import TranslationConflict, TranslationError, TranslationManager, _apply_marked, _marked, _xml, validate_translation


def make_epub(path):
    with zipfile.ZipFile(path, 'w') as z:
        z.writestr('mimetype', 'application/epub+zip')
        z.writestr('META-INF/container.xml', '''<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="OEBPS/content.opf"/></rootfiles></container>''')
        z.writestr('OEBPS/content.opf', '''<package xmlns="http://www.idpf.org/2007/opf"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:identifier>original</dc:identifier><dc:title>Original</dc:title><dc:language>en</dc:language></metadata><manifest><item id="c1" href="one.xhtml" media-type="application/xhtml+xml"/><item id="c2" href="two.xhtml" media-type="application/xhtml+xml"/><item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/><item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/><item id="img" href="cover.png" media-type="image/png"/></manifest><spine toc="ncx"><itemref idref="c1"/><itemref idref="c2"/></spine></package>''')
        z.writestr('OEBPS/cover.png', b'not an actual image')
        z.writestr('OEBPS/one.xhtml', '''<html xmlns="http://www.w3.org/1999/xhtml"><body><h1>First</h1><p>Hello <em>old <strong>friend</strong></em> and <a href="two.xhtml#note">note</a>.</p><img src="cover.png" alt="A tree"/></body></html>''')
        z.writestr('OEBPS/two.xhtml', '''<html xmlns="http://www.w3.org/1999/xhtml"><body><h1>Second</h1><p id="note">Footnote text.</p></body></html>''')
        z.writestr('OEBPS/nav.xhtml', '''<html xmlns="http://www.w3.org/1999/xhtml"><body><nav><ol><li><a href="one.xhtml">First</a></li></ol></nav></body></html>''')
        z.writestr('OEBPS/toc.ncx', '''<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/"><navMap><navPoint id="n1"><navLabel><text>First</text></navLabel><content src="one.xhtml"/></navPoint></navMap></ncx>''')


class FakeProvider:
    def generate(self, *, context, **_kwargs):
        class Value:
            def model_dump(self):
                return {'translations': [{'id': s['id'], 'text': 'DE ' + s['text']} for s in context['segments']]}
        return AIResult(Value(), 100, 100)


def wait_status(manager, job_id, expected):
    for _ in range(100):
        job = manager.public(job_id)
        if job['status'] in expected:
            return job
        time.sleep(.02)
    raise AssertionError(manager.public(job_id))


def test_nested_markers_preserve_links_and_formatting():
    node = ET.fromstring('<p>Hello <em>old <strong>friend</strong></em> and <a href="x">note</a>.</p>')
    source = _marked(node)
    validate_translation({'kind': 'content', 'source': source}, 'Hallo [[0]]alter [[0]]Freund[[/0]][[/0]] und [[1]]Anmerkung[[/1]].')
    _apply_marked(node, 'Hallo [[0]]alter [[0]]Freund[[/0]][[/0]] und [[1]]Anmerkung[[/1]].')
    assert node.find('a').attrib['href'] == 'x'
    assert ''.join(node.itertext()) == 'Hallo alter Freund und Anmerkung.'
    try:
        validate_translation({'kind': 'content', 'source': source}, 'marker missing')
    except TranslationError:
        pass
    else:
        raise AssertionError('missing markers accepted')


def test_xml_entities_are_rejected():
    with pytest.raises(TranslationError):
        _xml(b'<!DOCTYPE x [<!ENTITY a "boom">]><x>&a;</x>')


def test_inline_code_is_protected():
    node = ET.fromstring('<p>Run <code>rm -rf /</code> carefully.</p>')
    assert _marked(node) == 'Run [[0]][[/0]] carefully.'
    _apply_marked(node, 'Führe [[0]][[/0]] vorsichtig aus.')
    assert node.find('code').text == 'rm -rf /'


def test_translation_rejects_provider_change_on_resume(db_context):
    settings, factory = db_context
    with factory() as db:
        book = add_book(db)
        book.language = 'en'
        path = settings.library_dir / book.library_path
        path.parent.mkdir(parents=True)
        make_epub(path)
        book.file_size = path.stat().st_size
        book.sha256 = sha256_file(path)
        db.commit()
    manager = TranslationManager(settings, factory)
    job = manager.create(book.id, 'de', 'schnell', None)
    settings.ai_provider = 'custom'
    with pytest.raises(TranslationError, match='Anbindung'):
        manager.start(job['id'])


def test_distinct_translation_models_need_their_own_prices(db_context):
    settings, factory = db_context
    settings.ai_translation_input_usd_per_million = 2
    settings.ai_translation_output_usd_per_million = 8
    settings.ai_translation_qa_model = 'different-qa-model'
    manager = TranslationManager(settings, factory)
    assert manager._rates()[:2] == [[2, 8], [0, 0]]
    settings.ai_translation_qa_model = settings.ai_translation_model
    assert manager._rates()[1] == [2, 8]


@pytest.mark.parametrize('profile', ['schnell', 'buch', 'literarisch'])
def test_preview_resume_and_archive_epub(db_context, monkeypatch, profile):
    settings, factory = db_context
    with factory() as db:
        book = add_book(db)
        book.language = 'en'
        path = settings.library_dir / book.library_path
        path.parent.mkdir(parents=True)
        make_epub(path)
        book.file_size = path.stat().st_size
        book.sha256 = sha256_file(path)
        db.commit()
    monkeypatch.setattr('backend.translation.make_ai_provider', lambda _settings: FakeProvider())
    manager = TranslationManager(settings, factory)
    job = manager.create(book.id, 'de', profile, None)
    assert job['total_segments'] >= 6
    manager.start(job['id'])
    preview = wait_status(manager, job['id'], {'awaiting_glossary', 'failed'})
    assert preview['status'] == 'awaiting_glossary', preview
    assert preview['preview']
    manager.update_glossary(job['id'], [{'source': 'friend', 'target': 'Freund'}], '')
    manager.start(job['id'])
    completed = wait_status(manager, job['id'], {'completed', 'failed'})
    assert completed['status'] == 'completed', completed
    assert completed['output_book_id'] != book.id
    with factory() as db:
        output = db.get(Book, completed['output_book_id'])
        assert output.language == 'de'
        assert output.isbn is None
        assert output.reference_isbn == book.isbn
        assert len(output.id) == 35
        assert output.library_path == f"{output.id[3:7]}/{output.id}/book.epub"
        assert json.loads((settings.library_dir / output.library_path).parent.joinpath('metadata.json').read_text())['file']['filename'] == Path(output.library_path).name
        with zipfile.ZipFile(settings.library_dir / output.library_path) as z:
            chapter = ET.fromstring(z.read('OEBPS/one.xhtml'))
            assert 'DE Hello' in ''.join(chapter.itertext())
            assert chapter.find('.//{http://www.w3.org/1999/xhtml}a').attrib['href'] == 'two.xhtml#note'
            opf = ET.fromstring(z.read('OEBPS/content.opf'))
            assert opf.find('.//{http://purl.org/dc/elements/1.1/}language').text == 'de'
            ncx = ET.fromstring(z.read('OEBPS/toc.ncx'))
            assert ncx.find('.//{http://www.daisy.org/z3986/2005/ncx/}text').text == 'DE First'


def test_budget_pauses_before_request_and_can_be_raised(db_context, monkeypatch):
    settings, factory = db_context
    settings.ai_translation_input_usd_per_million = 100
    settings.ai_translation_output_usd_per_million = 100
    with factory() as db:
        book = add_book(db)
        book.language = 'en'
        path = settings.library_dir / book.library_path
        path.parent.mkdir(parents=True)
        make_epub(path)
        book.sha256 = sha256_file(path)
        db.commit()
    calls = []
    class CountingProvider(FakeProvider):
        def generate(self, **kwargs):
            calls.append(1)
            return super().generate(**kwargs)
    monkeypatch.setattr('backend.translation.make_ai_provider', lambda _settings: CountingProvider())
    manager = TranslationManager(settings, factory)
    job = manager.create(book.id, 'de', 'schnell', 0.0001)
    manager.start(job['id'])
    paused = wait_status(manager, job['id'], {'paused', 'failed'})
    assert paused['status'] == 'paused'
    assert not calls
    manager.update_budget(job['id'], None)
    manager.start(job['id'])
    assert wait_status(manager, job['id'], {'awaiting_glossary', 'failed'})['status'] == 'awaiting_glossary'
    assert calls


def test_ebooklib_epub_stays_readable(db_context, monkeypatch):
    from ebooklib import epub
    from backend.tests.test_language import write_epub
    settings, factory = db_context
    with factory() as db:
        book = add_book(db)
        book.language = 'en'
        path = settings.library_dir / book.library_path
        path.parent.mkdir(parents=True)
        write_epub(path)
        book.sha256 = sha256_file(path)
        db.commit()
    monkeypatch.setattr('backend.translation.make_ai_provider', lambda _settings: FakeProvider())
    manager = TranslationManager(settings, factory)
    job = manager.create(book.id, 'de', 'schnell', None)
    assert manager.get(job['id'])['first_file'].endswith('chapter0.xhtml')
    manager.start(job['id'])
    assert wait_status(manager, job['id'], {'awaiting_glossary', 'failed'})['status'] == 'awaiting_glossary'
    manager.start(job['id'])
    completed = wait_status(manager, job['id'], {'completed', 'failed'})
    assert completed['status'] == 'completed', completed
    with factory() as db:
        output = db.get(Book, completed['output_book_id'])
        parsed = epub.read_epub(str(settings.library_dir / output.library_path))
        assert parsed.get_metadata('DC', 'language')[0][0] == 'de'
    cover_manager = ImportManager(settings, factory, ProviderChain([]), CoverService([]))
    cover_manager._store_refreshed_cover(completed['output_book_id'],
                                        CoverAsset(b'cover-data', '.png', 'image/png', 1, 1, 'external'))
    with factory() as db:
        output = db.get(Book, completed['output_book_id'])
        document = json.loads(output.metadata_json)
        assert document['file']['cover_filename'] == 'cover.png'
        assert (settings.library_dir / output.cover_path).read_bytes() == b'cover-data'


def test_paused_job_rejects_stale_worker_write(db_context):
    settings, factory = db_context
    with factory() as db:
        book = add_book(db)
        book.language = 'en'
        path = settings.library_dir / book.library_path
        path.parent.mkdir(parents=True)
        make_epub(path)
        book.sha256 = sha256_file(path)
        db.commit()
    manager = TranslationManager(settings, factory)
    job_id = manager.create(book.id, 'de', 'schnell', None)['id']
    stale = manager.get(job_id)
    manager.stop(job_id)
    stale['status'] = 'translating'
    with pytest.raises(TranslationConflict):
        manager._save(stale)
    assert manager.public(job_id)['status'] == 'paused'


def test_pause_during_paid_request_keeps_result_without_resuming(db_context, monkeypatch):
    settings, factory = db_context
    with factory() as db:
        book = add_book(db)
        book.language = 'en'
        path = settings.library_dir / book.library_path
        path.parent.mkdir(parents=True)
        make_epub(path)
        book.sha256 = sha256_file(path)
        db.commit()
    entered, release = Event(), Event()

    class BlockingProvider(FakeProvider):
        def generate(self, **kwargs):
            entered.set()
            assert release.wait(3)
            return super().generate(**kwargs)

    monkeypatch.setattr('backend.translation.make_ai_provider', lambda _settings: BlockingProvider())
    manager = TranslationManager(settings, factory)
    job_id = manager.create(book.id, 'de', 'schnell', None)['id']
    manager.start(job_id)
    assert entered.wait(3)
    manager.stop(job_id)
    release.set()
    for _ in range(100):
        if job_id not in manager.running:
            break
        time.sleep(.02)
    job = manager.public(job_id)
    assert job['status'] == 'paused'
    assert job['completed_segments'] > 0


def test_failed_final_commit_does_not_leave_orphan_book(db_context):
    settings, factory = db_context
    with factory() as db:
        book = add_book(db)
        book.language = 'en'
        path = settings.library_dir / book.library_path
        path.parent.mkdir(parents=True)
        make_epub(path)
        book.sha256 = sha256_file(path)
        db.commit()
    manager = TranslationManager(settings, factory)
    job_id = manager.create(book.id, 'de', 'schnell', None)['id']
    job = manager.get(job_id)
    job['translations'] = {segment['id']: segment['source'] for segment in job['segments']}
    manager._save(job)
    engine = factory.kw['bind']

    def reject_completion(_connection, _cursor, statement, parameters, _context, _executemany):
        if 'UPDATE translation_jobs SET' in statement and 'completed' in str(parameters):
            raise RuntimeError('injected commit failure')

    event.listen(engine, 'before_cursor_execute', reject_completion)
    try:
        with pytest.raises(RuntimeError):
            manager._assemble(job)
    finally:
        event.remove(engine, 'before_cursor_execute', reject_completion)
    with factory() as db:
        assert list(db.scalars(select(Book.id))) == [book.id]


def test_segment_repair_runs_all_profile_passes_and_respects_budget(db_context, monkeypatch):
    settings, factory = db_context
    with factory() as db:
        book = add_book(db)
        book.language = 'en'
        path = settings.library_dir / book.library_path
        path.parent.mkdir(parents=True)
        make_epub(path)
        book.sha256 = sha256_file(path)
        db.commit()
    calls = []
    class CountingProvider(FakeProvider):
        def generate(self, **kwargs):
            calls.append(kwargs['model'])
            return super().generate(**kwargs)
    monkeypatch.setattr('backend.translation.make_ai_provider', lambda _settings: CountingProvider())
    manager = TranslationManager(settings, factory)
    job_id = manager.create(book.id, 'de', 'literarisch', None)['id']
    current = manager.get(job_id)
    segment_id = current['segments'][0]['id']
    current['drafts'][segment_id] = {'stage': 1, 'text': current['segments'][0]['source']}
    manager._save(current)
    manager.stop(job_id)
    repaired = manager.repair_segment(job_id, segment_id)
    assert repaired['completed_segments'] == 1
    assert calls == manager.get(job_id)['models']
    assert len(manager.get(job_id)['revisions'][segment_id]) == 3

    settings.ai_translation_input_usd_per_million = 100
    settings.ai_translation_output_usd_per_million = 100
    limited = manager.create(book.id, 'de', 'schnell', 0.0001)
    current = manager.get(limited['id'])
    limited_segment = current['segments'][0]['id']
    current['drafts'][limited_segment] = {'stage': 1, 'text': current['segments'][0]['source']}
    manager._save(current)
    manager.stop(limited['id'])
    with pytest.raises(TranslationError, match='Auftragsbudget'):
        manager.repair_segment(limited['id'], limited_segment)
