from __future__ import annotations

try:
    import torch
    import torch.nn as nn
except ImportError as exc:
    raise ImportError(
        "Install via: pip install -r requirements-torch.txt"
    ) from exc


class DhompoLSTM(nn.Module):
    """Multi-output LSTM for 5-horizon flood prediction.

    Parameters
    ----------
    input_size:
        Number of features per timestep (default 160 from feature engineering).
    hidden_size:
        LSTM hidden state dimension.
    num_layers:
        Number of stacked LSTM layers.
    output_size:
        Number of forecast horizons (default 5 for h1–h5).
    dropout:
        Dropout probability between LSTM layers (applied if num_layers > 1).
    """

    def __init__(
        self,
        input_size: int = 160,
        hidden_size: int = 128,
        num_layers: int = 2,
        output_size: int = 5,
        dropout: float = 0.2,
    ) -> None:
        super().__init__()
        self.hidden_size = hidden_size
        self.num_layers = num_layers

        self.lstm = nn.LSTM(
            input_size=input_size,
            hidden_size=hidden_size,
            num_layers=num_layers,
            batch_first=True,
            dropout=dropout if num_layers > 1 else 0.0,
        )
        self.fc = nn.Linear(hidden_size, output_size)

    def forward(self, x: "torch.Tensor") -> "torch.Tensor":
        """Forward pass.

        Parameters
        ----------
        x:
            Input tensor of shape (batch, seq_len, input_size).

        Returns
        -------
        torch.Tensor
            Output of shape (batch, output_size) — predictions for all horizons.
        """
        lstm_out, _ = self.lstm(x)
        # Use the last timestep's hidden state
        out = self.fc(lstm_out[:, -1, :])
        return out
