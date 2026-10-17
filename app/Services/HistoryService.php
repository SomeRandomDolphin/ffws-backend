<?php

namespace App\Services;

interface HistoryService
{
    public function getHistory(int $offsetReq, int $limitReq, ?string $daerah);
    public function getHistoryPrediction(int $offset, int $limit, ?string $daerah = null);
    public function getChartHistory(?string $daerah, int|string|null $periode);
}