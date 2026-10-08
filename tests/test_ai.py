import json

import anthropic
import httpx
import httpx2
import pytest
from fastapi.testclient import TestClient

from jobapplier import ai
from jobapplier.app import create_app
from jobapplier.store import Store


@pytest.fixture
def setup(tmp_path, monkeypatch):
    store = Store(tmp_path)
    store.set('settings', {**store.settings(), 'provider': 'anthropic', 'model': 'chosen-model'})
    monkeypatch.setattr(ai, 'secret', lambda name: 'test-key')
    original = httpx.AsyncClient
    requests = []

    def install(cloud, local_handler=None):
        def hosted(request):
            requests.append(request)
            return cloud(request)

        def local(request):
            requests.append(request)
            return (local_handler or (lambda r: local_ok()))(request)
        monkeypatch.setattr(ai, '_client', lambda key: anthropic.AsyncAnthropic(
            api_key=key, max_retries=0,
            http_client=anthropic.DefaultAsyncHttpxClient(transport=httpx2.MockTransport(hosted))))
        monkeypatch.setattr(ai.httpx, 'AsyncClient',
                            lambda **kwargs: original(transport=httpx.MockTransport(local), **kwargs))
    return store, install, requests


def error(status, kind):
    return httpx2.Response(status, json={'type': 'error', 'error': {'type': kind, 'message': kind}})


def billing():
    return error(402, 'billing_error')


def message(text='{"ok":true}', stop='end_turn'):
    return httpx2.Response(200, json={
        'id': 'msg_1', 'type': 'message', 'role': 'assistant', 'model': 'chosen-model',
        'content': [{'type': 'text', 'text': text}], 'stop_reason': stop, 'stop_sequence': None,
        'usage': {'input_tokens': 10, 'output_tokens': 5, 'cache_read_input_tokens': 7,
                  'cache_creation_input_tokens': 0}})


def local_ok():
    return httpx.Response(200, json={'message': {'content': '{"ok":true}'}, 'prompt_eval_count': 10, 'eval_count': 5})


async def test_fallback_reuses_same_input_and_stays_local_for_run(setup):
    store, install, requests = setup
    install(lambda r: billing())
    model = ai.Model(store)
    assert await model.json('Extract facts', {'source': 'verified'}, stable={'profile': 'p'}) == {'ok': True}
    await model.json('Second step', {})
    assert [r.url.host for r in requests] == ['api.anthropic.com', '127.0.0.1', '127.0.0.1']
    cloud, fallback = [json.loads(r.content) for r in requests[:2]]
    assert [json.loads(b['text']) for b in cloud['messages'][0]['content']] == [{'profile': 'p'}, {'source': 'verified'}]
    assert json.loads(fallback['messages'][1]['content']) == {'profile': 'p', 'source': 'verified'}
    assert 'Extract facts' in fallback['messages'][0]['content']
    assert fallback['model'] == 'qwen3.5:9b-q4_K_M'
    assert fallback['options']['num_ctx'] == 16384
    assert requests[1].extensions['timeout']['read'] == 600
    assert 'x-api-key' not in requests[1].headers and 'authorization' not in requests[1].headers
    assert store.get('last_inference')['fallback'] is True
    assert store.events()
    await ai.Model(store).json('New run', {})
    assert requests[3].url.host == 'api.anthropic.com'


async def test_request_shape_uses_cache_effort_and_refusal_fallback(setup):
    store, install, requests = setup
    install(lambda r: message())
    assert await ai.Model(store).json('Task', {'step': 1}, stable={'profile': {'b': 1, 'a': 2}}) == {'ok': True}
    body = json.loads(requests[0].content)
    assert body['system'][0]['cache_control'] == {'type': 'ephemeral'}
    assert body['messages'][0]['content'][0]['cache_control'] == {'type': 'ephemeral'}
    assert body['messages'][0]['content'][0]['text'] == '{"profile": {"a": 2, "b": 1}}'
    assert body['output_config'] == {'effort': 'low'}
    assert body['fallbacks'] == 'default'
    assert 'server-side-fallback-2026-07-01' in requests[0].headers['anthropic-beta']
    assert store.get('last_inference')['provider'] == 'anthropic'
    assert store.get('usage')['cache_read_tokens'] == 7
    assert len(requests) == 1


async def test_browser_decisions_pause_instead_of_running_locally(setup):
    store, install, requests = setup
    install(lambda r: billing())
    model = ai.Model(store)
    with pytest.raises(ValueError, match='Form filling pauses'):
        await model.json('Choose action', {}, local_ok=False)
    assert await model.json('Research', {}) == {'ok': True}
    with pytest.raises(ValueError, match='Form filling pauses'):
        await model.json('Choose action', {}, local_ok=False)
    assert [r.url.host for r in requests] == ['api.anthropic.com', '127.0.0.1']


@pytest.mark.parametrize('status,kind', [(401, 'authentication_error'), (429, 'rate_limit_error'),
                                         (529, 'overloaded_error'), (404, 'not_found_error')])
async def test_other_errors_do_not_fallback(setup, status, kind):
    store, install, requests = setup
    install(lambda r: error(status, kind))
    with pytest.raises(ValueError, match='Claude request failed'):
        await ai.Model(store).json('Task', {})
    assert len(requests) == 1


async def test_old_credit_balance_message_is_recognized(setup):
    store, install, requests = setup
    install(lambda r: httpx2.Response(400, json={'type': 'error', 'error': {
        'type': 'invalid_request_error', 'message': 'Your credit balance is too low to access the Anthropic API.'}}))
    assert await ai.Model(store).json('Task', {}) == {'ok': True}


async def test_disabled_fallback(setup):
    store, install, _ = setup
    store.set('settings', {**store.settings(), 'fallback_enabled': False})
    install(lambda r: billing())
    with pytest.raises(ValueError, match='enable the local fallback'):
        await ai.Model(store).json('Task', {})


async def test_missing_local_model_has_actionable_error(setup):
    store, install, _ = setup
    install(lambda r: billing(), lambda r: httpx.Response(404, json={'error': 'model missing'}))
    with pytest.raises(ValueError, match='fallback is unavailable'):
        await ai.Model(store).json('Task', {})


async def test_malformed_fallback_cannot_become_browser_action(setup):
    store, install, _ = setup
    install(lambda r: billing(), lambda r: httpx.Response(200, json={'message': {'content': 'not JSON'}}))
    with pytest.raises(ValueError, match='No browser action'):
        await ai.Model(store).json('Task', {})


@pytest.mark.parametrize('stop,match', [('max_tokens', 'output limit'), ('refusal', 'declined')])
async def test_truncated_or_refused_output_not_used(setup, stop, match):
    store, install, _ = setup
    install(lambda r: message(stop=stop))
    with pytest.raises(ValueError, match=match):
        await ai.Model(store).json('Task', {})


async def test_fenced_json_is_accepted_and_invalid_rejected(setup):
    store, install, _ = setup
    install(lambda r: message('```json\n{"ok": true}\n```'))
    assert await ai.Model(store).json('Task', {}) == {'ok': True}
    install(lambda r: message('Sure! here it is'))
    with pytest.raises(ValueError, match='invalid response'):
        await ai.Model(store).json('Task', {})


def test_legacy_openai_settings_migrate(tmp_path):
    store = Store(tmp_path)
    store.set('settings', {'provider': 'openai', 'model': 'gpt-5.6-terra', 'openai_project': 'proj_x', 'sheet_tab': 'Jobs', 'tex_main': 'cv.tex'})
    settings = store.settings()
    assert settings['provider'] == 'anthropic' and settings['model'] == 'claude-sonnet-5-5'
    assert 'openai_project' not in settings and 'sheet_tab' not in settings and settings['tex_main'] == 'cv.tex'


def test_removed_agent_routes_and_settings_validation(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        token = client.get('/api/bootstrap').json()['token']
        h = {'x-workspace-token': token}
        assert client.get('/api/agent', headers=h).status_code == 404
        for changes in [{'fallback_enabled': 'true'}, {'ollama_context': 999999}, {'ollama_timeout': 1},
                        {'ollama_url': 'https://remote.example'}, {'provider': 'openai'}, {'effort': 'max'},
                        {'openai_project': 'proj_x'}]:
            assert client.put('/api/settings', headers=h, json=changes).status_code == 400
        assert client.put('/api/settings', headers=h, json={'fallback_enabled': True, 'ollama_context': 16384,
                                                             'effort': 'medium'}).status_code == 200
        assert client.post('/api/secrets', headers=h, json={'name': 'openai_key', 'value': 'x'}).status_code == 400


async def test_local_stream_reports_progress(setup):
    store, install, _ = setup
    store.set('settings', {**store.settings(), 'provider': 'ollama', 'model': 'qwen'})
    lines = [{'message': {'content': '{"ok":'}}, {'message': {'content': ' true}'}},
             {'message': {'content': ''}, 'done': True, 'done_reason': 'stop', 'prompt_eval_count': 9, 'eval_count': 2}]
    install(lambda r: billing(), lambda r: httpx.Response(200, text='\n'.join(json.dumps(x) for x in lines)))
    seen = []
    model = ai.Model(store, on_activity=lambda state: seen.append(state and (state['phase'], state.get('tokens'))))
    assert await model.json('Task', {}) == {'ok': True}
    assert seen == [('loading', None), ('generating', 1), None]
    assert store.get('usage')['output_tokens'] == 2


async def test_stopped_ollama_is_started_once(setup, monkeypatch):
    store, install, requests = setup
    store.set('settings', {**store.settings(), 'provider': 'ollama', 'model': 'qwen'})
    started = []

    def local(request):
        if not started:
            raise httpx.ConnectError('refused', request=request)
        return local_ok()

    async def fake_start(base, wait=30):
        started.append(base)
        return True
    monkeypatch.setattr(ai, 'start_ollama', fake_start)
    install(lambda r: billing(), local)
    assert await ai.Model(store).json('Task', {}) == {'ok': True}
    assert started == ['http://127.0.0.1:11434']
    assert any('Started Ollama' in e['message'] for e in store.events())


async def test_ollama_that_cannot_start_reports_clearly(setup, monkeypatch):
    store, install, _ = setup
    store.set('settings', {**store.settings(), 'provider': 'ollama', 'model': 'qwen'})

    async def fake_start(base, wait=30):
        return False
    monkeypatch.setattr(ai, 'start_ollama', fake_start)
    install(lambda r: billing(), lambda r: (_ for _ in ()).throw(httpx.ConnectError('refused', request=r)))
    with pytest.raises(ValueError, match='could not be started'):
        await ai.Model(store).json('Task', {})


def test_context_grows_for_long_inputs_and_rejects_oversized():
    small = [{'role': 'user', 'content': 'x' * 3000}]
    assert ai.context_size(small, 4000, 16384) == 16384
    big = [{'role': 'user', 'content': 'x' * 45000}]
    assert ai.context_size(big, 4000, 16384) == 20480
    with pytest.raises(ValueError, match='too large'):
        ai.context_size([{'role': 'user', 'content': 'x' * 120000}], 4000, 16384)


@pytest.mark.parametrize('prompt,match', [(16000, 'ran out of context'), (500, 'output limit')])
async def test_truncated_local_answer_explains_which_limit(setup, prompt, match):
    store, install, _ = setup
    store.set('settings', {**store.settings(), 'provider': 'ollama', 'model': 'qwen'})
    install(lambda r: billing(), lambda r: httpx.Response(200, json={
        'message': {'content': '{"a":'}, 'done': True, 'done_reason': 'length', 'prompt_eval_count': prompt, 'eval_count': 400}))
    with pytest.raises(ValueError, match=match) as err:
        await ai.Model(store).json('Task', {})
    assert 'install' not in str(err.value)


async def test_ingest_proposes_only_new_lines(tmp_path, monkeypatch):
    from jobapplier.workflow import Workflow
    store = Store(tmp_path)
    store.save_profile_text(store.profile_text().replace("- Email: ", "- Email: me@example.com").replace("## Skills\n", "## Skills\n- Programming: Python\n"), "seed")
    seen = {}

    async def fake_json(self, task, data, stable=None, local_ok=True, fast=False):
        seen.update(data=data, stable=stable, fast=fast)
        return {"additions": {"Skills": ["- Programming: Python", "- Embedded: ESP32"], "Projects": ["**LINDA** — lab tool"], "Bogus": ["x"]},
                "identity": {"email": "other@example.com", "city": "Austin", "ssn": "no"}, "conflicts": ["Graduation year differs"]}
    monkeypatch.setattr(ai.Model, "json", fake_json)
    doc = {"id": "d1", "name": "cv.pdf", "kind": "resume", "text": "..."}
    result = await Workflow(store, None, None).ingest_document(doc)
    assert seen["fast"] is True and "current_profile" in seen["stable"] and seen["data"]["document"]["name"] == "cv.pdf"
    assert result["additions"] == {"Skills": ["- Programming: Python", "- Embedded: ESP32"], "Projects": ["**LINDA** — lab tool"]}
    assert result["identity"] == {"city": "Austin"}  # email kept as the user wrote it; ssn is not a profile key
    assert result["changes"] == 3 and "- Embedded: ESP32" in result["preview"] and "- **LINDA** — lab tool" in result["preview"]
    assert any("me@example.com" in c and "other@example.com" in c for c in result["conflicts"]) and "Graduation year differs" in result["conflicts"]


async def test_simple_tasks_use_fast_local_model_and_stay_loaded(setup):
    store, install, requests = setup
    store.set('settings', {**store.settings(), 'provider': 'ollama', 'model': 'qwen-9b', 'fast_model': 'qwen-4b'})
    install(lambda r: billing())
    await ai.Model(store).json('Read posting', {}, fast=True)
    await ai.Model(store).json('Tailor', {})
    bodies = [json.loads(r.content) for r in requests]
    assert [b['model'] for b in bodies] == ['qwen-4b', 'qwen-9b']
    assert all(b['keep_alive'] == '30m' for b in bodies)
    assert len({b['options']['num_ctx'] for b in bodies}) == 1  # same context size: no model reload between calls


class FakeCodex:
    def __init__(self, code=0, out=b'{"ok": true}', err=b'model: gpt-6-astra\ntokens used\n13,701\n'):
        self.returncode, self.out, self.err, self.calls = code, out, err, []

    async def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        fake = self

        class Proc:
            returncode = fake.returncode

            async def communicate(self, data):
                fake.stdin = data.decode()
                return fake.out, fake.err

            def kill(self):
                pass
        return Proc()


@pytest.fixture
def codex(setup, monkeypatch):
    store, install, requests = setup
    store.set('settings', {**store.settings(), 'provider': 'codex', 'model': 'default'})
    monkeypatch.setattr(ai.shutil, 'which', lambda name: '/usr/bin/codex')
    fake = FakeCodex()
    monkeypatch.setattr(ai.asyncio, 'create_subprocess_exec', fake)
    return store, install, requests, fake


async def test_codex_runs_read_only_without_saving_sessions(codex):
    store, _, _, fake = codex
    assert await ai.Model(store).json('Extract facts', {'page': 'x'}, stable={'profile': 'p'}) == {'ok': True}
    args, kwargs = fake.calls[0]
    assert args[:2] == ('/usr/bin/codex', 'exec')
    assert {'--ephemeral', '--skip-git-repo-check'} <= set(args) and args[args.index('--sandbox') + 1] == 'read-only'
    assert '-m' not in args and 'model_reasoning_effort=low' in args
    assert 'JobApplier' not in kwargs['cwd']  # runs in an empty temp folder, never the project
    assert 'Extract facts' in fake.stdin and '"profile": "p"' in fake.stdin
    assert store.get('last_inference')['provider'] == 'codex'
    assert store.get('usage')['input_tokens'] == 13701


async def test_codex_plan_limit_falls_back_for_text_and_pauses_forms(codex):
    store, install, requests, fake = codex
    fake.returncode, fake.err = 1, b"ERROR: You've hit your usage limit. Try again later."
    install(lambda r: billing())
    model = ai.Model(store)
    assert await model.json('Read posting', {}) == {'ok': True}
    assert [r.url.host for r in requests] == ['127.0.0.1']
    with pytest.raises(ValueError, match='ChatGPT plan'):
        await model.json('Choose action', {}, local_ok=False)


async def test_codex_errors_do_not_fall_back(codex):
    store, _, requests, fake = codex
    fake.returncode, fake.err = 1, b'ERROR: {"type":"error","error":{"message":"The model is overloaded"}}'
    with pytest.raises(ValueError, match='Codex failed: The model is overloaded'):
        await ai.Model(store).json('Task', {})
    assert requests == []
