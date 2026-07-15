from pathlib import Path


PATH = Path("scripts/pipeline.py")
OLD = '''            finally:
                if page is not None:
                    await page.close()
            finished_at = datetime.now()
'''
NEW = '''            finally:
                if page is not None:
                    try:
                        await page.close()
                    except Exception as exc:
                        result = {
                            **FAILED_RESULT,
                            "reason": f"unhandled page close exception: {exc}",
                        }
            finished_at = datetime.now()
'''

text = PATH.read_text(encoding="utf-8")
if text.count(OLD) != 1:
    raise RuntimeError(f"expected one page-close block, found {text.count(OLD)}")
PATH.write_text(text.replace(OLD, NEW, 1), encoding="utf-8")
