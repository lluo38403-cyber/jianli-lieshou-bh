from unittest.mock import patch
import yaml
from bosshunter.config import load_config, save_config
from bosshunter.ai import greeter
from bosshunter.db import get_db, insert_job, update_job_status


def test_default_generation_skips_review_but_explicit_opt_in_survives(tmp_path):
    config = load_config(tmp_path / 'missing.yaml')
    assert config['ai']['greeting_style_suggestions'] is False
    # AI 招呼语默认是关的（用固定招呼语）；本测试测的是“AI 生成时默认跳过质量检查”，
    # 所以先显式开启 AI 生成，review 默认关 = 恰好调一次（生成，不检查）。
    config['profile']['ai_greeting_enabled'] = True
    path = tmp_path / 'jobs.db'
    db = get_db(path)
    insert_job(db, {'id': 'one', 'title': '运营', 'company': '示例', 'salary': '16-25K'})
    update_job_status(db, 'one', 'approved')
    db.close()
    with patch.object(greeter, '_get_resume_summary', return_value='真实简历'), patch.object(greeter, '_call_claude', return_value='您好，我做过内容运营。') as call:
        assert greeter.generate_greetings(config, ['one'], path) == 1
    assert call.call_count == 1
    config['ai']['greeting_style_suggestions'] = True
    save_config(config, tmp_path / 'config.yaml')
    assert load_config(tmp_path / 'config.yaml')['ai']['greeting_style_suggestions'] is True
