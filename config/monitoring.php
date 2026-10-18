<?php

return [
    'predictions_enabled' => filter_var(env('PREDICTIONS_ENABLED', false), FILTER_VALIDATE_BOOLEAN),
    'telegram_enabled' => filter_var(env('TELEGRAM_ENABLED', false), FILTER_VALIDATE_BOOLEAN),
    'features' => [
        'auth' => ['available' => true, 'used_by_new_frontend' => false],
        'roles' => ['available' => true, 'used_by_new_frontend' => false],
        'contacts' => ['available' => true, 'used_by_new_frontend' => false],
        'station_configuration' => ['available' => true, 'used_by_new_frontend' => false],
        'excel_import' => ['available' => true, 'used_by_new_frontend' => false],
        'history' => ['available' => true, 'used_by_new_frontend' => false],
    ],
];
