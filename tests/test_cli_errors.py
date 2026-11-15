from academic_data_mcp.cli import main


def test_removed_csda_reports_client_config_fix_without_traceback(capsys):
    assert main(['serve', '--providers', 'whu,csda']) == 2
    captured = capsys.readouterr()
    assert not captured.out
    assert 'csda' in captured.err
    assert 'ADM_PROVIDERS' in captured.err
    assert 'whu' in captured.err
    assert 'Traceback' not in captured.err
