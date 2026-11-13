"""Default-off local entry. Untrusted parsing runs in a disposable bounded process."""
import html
import multiprocessing as mp
import time
import sys
import subprocess
from .models import Dataset, FileSpec, ParseSettings
from .parser import ParseFailure, parse_core

MAX_RESULT = 16 * 1024 * 1024


def _worker(channel, data, spec, settings):
    try:
        import resource
        resource.setrlimit(resource.RLIMIT_CPU, (3, 3))
        if sys.platform != "darwin":
            resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024, 512 * 1024 * 1024))
        result = parse_core(data, spec, settings).model_dump_json().encode()
        if len(result) > MAX_RESULT - 1:
            raise ParseFailure('result_limit')
        channel.send_bytes(b'+' + result)
    except ParseFailure as exc:
        channel.send_bytes(b'-' + exc.code.encode('ascii'))
    except MemoryError:
        channel.send_bytes(b'-resource_limit')
    except BaseException:
        # No exception text, traceback, filename, or source data crosses the boundary.
        channel.send_bytes(b'-invalid_file')
    finally:
        channel.close()


def parse_file(data: bytes, spec: FileSpec, settings: ParseSettings | None = None) -> Dataset:
    """Trusted local caller only. No file paths, persistence, authentication or network."""
    try:
        settings = ParseSettings.model_validate((settings or ParseSettings()).model_dump())
        spec = FileSpec.model_validate(spec.model_dump())
    except Exception:
        raise ParseFailure('invalid_contract') from None
    if not settings.enabled:
        raise ParseFailure('parsing_disabled')
    if type(data) is not bytes:
        raise ParseFailure('invalid_input')
    if len(data) > settings.max_bytes:
        raise ParseFailure('file_limit')
    context = mp.get_context('spawn')
    receiver, sender = context.Pipe(duplex=False)
    process = context.Process(target=_worker, args=(sender, data, spec, settings), daemon=True)
    started = time.monotonic()
    try:
        process.start()
        sender.close()
        while True:
            remaining = settings.wall_seconds - (time.monotonic()-started)
            if remaining <= 0:
                raise ParseFailure('parse_timeout')
            if sys.platform == 'darwin' and process.is_alive():
                check = subprocess.run(['/bin/ps', '-o', 'rss=', '-p', str(process.pid)], capture_output=True, timeout=min(remaining, 0.5), check=False)
                if check.returncode == 0:
                    if int(check.stdout.strip()) > 512 * 1024:
                        raise ParseFailure('resource_limit')
                elif process.is_alive():
                    raise ParseFailure('memory_guard_unavailable')
            if receiver.poll(min(0.02, remaining)):
                break
        payload = receiver.recv_bytes(MAX_RESULT)
        if payload.startswith(b'-'):
            raise ParseFailure(payload[1:].decode('ascii'))
        if not payload.startswith(b'+'):
            raise ParseFailure('invalid_result')
        return Dataset.model_validate_json(payload[1:])
    except ParseFailure:
        raise
    except Exception:
        raise ParseFailure('worker_failure') from None
    finally:
        receiver.close()
        sender.close()
        if process.pid is not None:
            process.join(timeout=0.1)
            if process.is_alive():
                process.terminate()
                process.join(timeout=0.2)
            if process.is_alive():
                process.kill()
                process.join()
            process.close()


def preview(dataset: Dataset, *, reveal_columns: tuple[int, ...] = ()) -> dict:
    """Local bounded preview, all labels/values redacted by default. Never CSV export.

    Column indices are one-based. The caller must authorize any reveal; this library
    does not grant access. Render returned strings as text, never HTML instructions.
    """
    if type(reveal_columns) is not tuple or any(type(i) is not int or not 1 <= i <= 8 for i in reveal_columns):
        raise ParseFailure('invalid_preview_policy')
    def safe(value):
        return html.escape(value[:128], quote=True)
    tables = []
    for table in dataset.tables[:4]:
        tables.append({
            'columns': [safe(c.name) if i in reveal_columns else '[redacted]' for i, c in enumerate(table.columns[:8], 1)],
            'rows': [[{'value': safe(c.value) if c.value is not None and i in reveal_columns else (None if c.value is None else '[redacted]'), 'kind':c.kind, 'row':c.location.row, 'column':i} for i,c in enumerate(row[:8],1)] for row in table.rows[:5]],
            'total_rows':len(table.rows), 'total_columns':len(table.columns),
        })
    return {'revision_id':dataset.revision_id, 'status':dataset.status, 'tables':tables, 'issue_codes':sorted({i.code for i in dataset.issues})}
