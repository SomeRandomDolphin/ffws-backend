<?php

namespace App\Services;

interface HistoryService
{
    public function getHistory($offsetReq, $limitReq, $daerah);
    public function getHistoryPrediction($offset, $limit, $daerah = null);
    public function getChartHistory($daerah, $periode);
}