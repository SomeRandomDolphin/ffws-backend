from __future__ import annotations

try:
    import torch
    import torch.nn as nn
except ImportError as exc:
    raise ImportError(
        "Install via: pip install -r requirements-torch.txt"
    ) from exc


class DhompoGRU(nn.Module):
    """GRU multi-output untuk prediksi 5 horizon banjir Dhompo."""

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

        self.gru = nn.GRU(
            input_size=input_size,
            hidden_size=hidden_size,
            num_layers=num_layers,
            batch_first=True,
            dropout=dropout if num_layers > 1 else 0.0,
        )
        self.fc = nn.Linear(hidden_size, output_size)

    def forward(self, x: "torch.Tensor") -> "torch.Tensor":
        # Pakai hidden state di timestep terakhir sebagai ringkasan sekuens.
        gru_out, _ = self.gru(x)
        out = self.fc(gru_out[:, -1, :])
        return out
