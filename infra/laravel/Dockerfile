FROM php:8.2-apache
RUN apt-get update && apt-get install -y --no-install-recommends libzip-dev libpng-dev libonig-dev libxml2-dev unzip \
    && docker-php-ext-install pdo_mysql zip gd mbstring bcmath exif \
    && a2enmod rewrite && rm -rf /var/lib/apt/lists/*
COPY --from=composer:2 /usr/bin/composer /usr/bin/composer
WORKDIR /var/www/html
COPY composer.json composer.lock ./
RUN composer install --no-dev --no-scripts --prefer-dist --no-interaction --no-progress
COPY app ./app
COPY bootstrap ./bootstrap
COPY config ./config
COPY database ./database
COPY public ./public
COPY resources ./resources
COPY routes ./routes
COPY lang ./lang
COPY artisan ./artisan
COPY infra/laravel/apache.conf /etc/apache2/sites-available/000-default.conf
RUN mkdir -p storage/app/public storage/framework/cache/data storage/framework/sessions storage/framework/views storage/logs \
    && composer dump-autoload --no-dev --optimize \
    && chown -R www-data:www-data storage bootstrap/cache
CMD ["apache2-foreground"]
