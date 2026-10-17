<?php

namespace App\Services\Impl;

use App\Repository\HistoryRepository;
use App\Services\HistoryService;
use Exception;
use Illuminate\Support\Facades\Log;
use RuntimeException;

class HistoryServiceImpl implements HistoryService
{
    protected HistoryRepository $historyRepository;

    public function __construct(HistoryRepository $historyRepository)
    {
        $this->historyRepository = $historyRepository;
    }

    public function getHistory(int $offsetReq, int $limitReq, ?string $daerah): array
    {
        try {
            return $this->historyRepository->getHistory($offsetReq, $limitReq, $daerah);
        } catch (Exception $e) {
            Log::error('Error: ' . $e->getMessage() . ' caused by: ' . ($e->getPrevious() ? $e->getPrevious()->getMessage() : 'No previous exception'), ['exception' => $e]);
            throw new RuntimeException($e->getMessage() . ' caused by: ' . $e->getPrevious(), $e->getCode(), $e);
        }
    }

    public function getHistoryPrediction(int $offset, int $limit, ?string $daerah = null): array
    {
        try {
            return $this->historyRepository->getHistoryPrediction($offset, $limit, $daerah);
        } catch (Exception $e) {
            Log::error('Error: ' . $e->getMessage() . ' caused by: ' . ($e->getPrevious() ? $e->getPrevious()->getMessage() : 'No previous exception'), ['exception' => $e]);
            throw new RuntimeException($e->getMessage() . ' caused by: ' . $e->getPrevious(), $e->getCode(), $e);
        }
    }

    public function getChartHistory(?string $daerah, int|string|null $periode): array
    {
        try {
            return $this->historyRepository->getChartHistory($daerah, $periode);
        } catch (Exception $e) {
            Log::error('Error: ' . $e->getMessage() . ' caused by: ' . ($e->getPrevious() ? $e->getPrevious()->getMessage() : 'No previous exception'), ['exception' => $e]);
            throw new RuntimeException($e->getMessage() . ' caused by: ' . $e->getPrevious(), $e->getCode(), $e);
        }
    }
}