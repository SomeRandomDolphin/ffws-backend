<?php

namespace Database\Seeders;

use Illuminate\Database\Seeder;

class DatabaseSeeder extends Seeder
{
    /**
     * Seed the application's database.
     *
     * NOTE: if a DatabaseSeeder.php already exists in this project with
     * other seeders registered (e.g. an admin user seeder, notification
     * contacts, etc.), merge this list into that file's call() array
     * instead of overwriting it wholesale — this version only knows
     * about the three seeders that were shared in this conversation.
     */
    public function run(): void
    {
        $this->call([
            UsersRoleSeeder::class,
            StasiunAirPosSeeder::class,
            StasiunHujanPosSeeder::class,
        ]);
    }
}