<?php

namespace App\Providers;

use Illuminate\Support\ServiceProvider;

class HistoryServiceProvider extends ServiceProvider
{
    /**
     * Register services.
     *
     * @return void
     */
    public function register()
    {
        $this->app->bind('App\Services\HistoryService', 'App\Services\Impl\HistoryServiceImpl');
        // No binding needed for App\Repository\HistoryRepository — it's a
        // concrete class with no constructor dependencies, so Laravel's
        // container resolves it automatically. (An earlier pass here
        // wrongly introduced an interface + this binding; both are
        // reverted now that the real, concrete HistoryRepository is back.)
    }

    /**
     * Bootstrap services.
     *
     * @return void
     */
    public function boot()
    {
        //
    }
}