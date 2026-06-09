import pytest
import asyncio
import time
import sys
from unittest.mock import MagicMock, patch
from vorte import Vorte
from vorte.cli.main import cli

@pytest.mark.asyncio
async def test_event_loop_lag_detection():
    """Test that event loop lag is detected and logged."""
    app = Vorte(auto_load=False)
    
    mock_logger = MagicMock()
    with patch("logging.getLogger", return_value=mock_logger) as mock_get_logger:
        # We will run the lag detector background task briefly
        task = asyncio.create_task(app._detect_event_loop_lag())
        
        # Let the loop run to initialize the task
        await asyncio.sleep(0.1)
        
        # Now block the thread to simulate GIL starvation
        time.sleep(0.7)
        
        # Give the event loop time to resume and execute the check logic
        await asyncio.sleep(0.2)
            
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
            
    mock_get_logger.assert_called_with("vorte.core")
    mock_logger.warning.assert_called()
    called_msg = mock_logger.warning.call_args[0][0]
    assert "Event loop lag detected" in called_msg
    assert "GIL starvation" in called_msg


def test_cli_serve_no_watch():
    """Test that serve without watch calls engine run directly."""
    mock_engine_instance = MagicMock()
    mock_engine_cls = MagicMock(return_value=mock_engine_instance)
    
    # Pre-mock the import of main so the import succeeds immediately
    mock_import = MagicMock()
    mock_app = MagicMock()
    mock_import.app = mock_app
    
    with patch("vorte.engine.VorteEngine", mock_engine_cls), \
         patch("sys.modules", {**sys.modules, "main": mock_import}), \
         patch("sys.argv", ["vorte", "serve", "--host=127.0.0.1", "--port=9999"]):
         
        try:
            cli()
        except SystemExit:
            pass
            
        mock_engine_cls.assert_called_once_with(mock_app)
        mock_engine_instance.run.assert_called_once_with(host="127.0.0.1", port=9999, workers=1)


def test_cli_serve_watch():
    """Test that serve with watch calls watchfiles.run_process."""
    mock_run_process = MagicMock()
    
    with patch("watchfiles.run_process", mock_run_process), \
         patch("sys.argv", ["vorte", "serve", "--watch", "--host=127.0.0.1", "--port=9999"]):
         
        try:
            cli()
        except SystemExit:
            pass
            
        mock_run_process.assert_called_once()
        args, kwargs = mock_run_process.call_args
        assert kwargs["target_type"] == "command"
        assert "vorte.cli.main serve" in kwargs["target"]
        assert "--host=127.0.0.1" in kwargs["target"]
        assert "--port=9999" in kwargs["target"]
