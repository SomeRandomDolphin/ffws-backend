<?php

namespace App\Http\Controllers;

use App\Http\Response\Response;
use App\Services\HistoryService;
use Illuminate\Http\JsonResponse;
use Illuminate\Http\Request;

class HistoryController extends Controller
{
    protected HistoryService $historyService;

    public function __construct(HistoryService $historyService)
    {
        $this->historyService = $historyService;
    }

    public function getHistory(Request $request): JsonResponse
    {
        $offset = $request->input('offset');
        $limit = $request->input('limit');
        $daerah = $request->input('daerah');
        $result = $this->historyService->getHistory($offset, $limit, $daerah);
        return response()->json(Response::success($result, 'Get All History successfully', 200));
    }

    public function getHistoryPrediction(Request $request): JsonResponse
    {
        $offset = $request->input('offset');
        $limit = $request->input('limit');
        // Optional: omit to get predictions across all predicted daerah.
        $daerah = $request->input('daerah');
        $result = $this->historyService->getHistoryPrediction($offset, $limit, $daerah);
        return response()->json(Response::success($result, 'Get All History successfully', 200));
    }

    public function getChartData(Request $request): JsonResponse
    {
        // `model` is no longer accepted: the ML API selects one model per
        // horizon server-side, there's nothing left for a client to pick.
        // `daerah` is kept (only "dhompo" is valid today) so additional
        // predicted stations can be added later without another breaking
        // change to this endpoint's contract.
        $daerah = $request->input('daerah');
        $periode = $request->input('periode');
        $result = $this->historyService->getChartHistory($daerah, $periode);
        return response()->json(Response::success($result, "Get last 24 hours data and next requested hour's prediction successfully", 200));
    }
}