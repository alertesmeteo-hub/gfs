<?php
/**
 * Plugin Name: GFS / NOAA France — Tableaux et cartes
 * Plugin URI: https://github.com/alertesmeteo-hub/gfs
 * Description: Cartes interactives et prévisions du modèle déterministe NOAA GFS pour la France métropolitaine et la Corse.
 * Version: 1.1.0
 * Author: Alertes Météo Hub
 * Requires at least: 5.8
 * Requires PHP: 7.4
 * License: GPL-2.0-or-later
 */

if (!defined('ABSPATH')) {
    exit;
}

define('GFS_VERSION', '1.1.0');
define('GFS_RELEASE_DATE', '27/08/2026');
define('GFS_OPTION_BASE_URL', 'gfs_national_data_base_url');
define(
    'GFS_DEFAULT_BASE_URL',
    'https://raw.githubusercontent.com/alertesmeteo-hub/gfs/data'
);

add_action('wp_enqueue_scripts', 'gfs_register_assets');
add_action('admin_init', 'gfs_register_settings');
add_action('admin_menu', 'gfs_add_settings_page');
add_shortcode('gfs_meteo', 'gfs_render_shortcode');
add_filter('plugin_action_links_' . plugin_basename(__FILE__), 'gfs_plugin_action_links');

function gfs_plugin_action_links($links) {
    $settings_link = sprintf(
        '<a href="%s">%s</a>',
        esc_url(admin_url('options-general.php?page=gfs-noaa')),
        esc_html__('Réglages', 'gfs-noaa-france')
    );
    array_unshift($links, $settings_link);

    $help_link = sprintf(
        '<a href="%s">%s</a>',
        esc_url(admin_url('options-general.php?page=gfs-noaa')),
        esc_html__('Shortcodes / Aide', 'gfs-noaa-france')
    );
    array_unshift($links, $help_link);

    return $links;
}

function gfs_register_assets() {
    wp_register_style(
        'gfs-table',
        plugin_dir_url(__FILE__) . 'assets/gfs-meteo.css',
        array(),
        GFS_VERSION
    );
    wp_register_script(
        'gfs-table',
        plugin_dir_url(__FILE__) . 'assets/gfs-meteo.js',
        array(),
        GFS_VERSION,
        true
    );
    wp_register_style(
        'gfs-map',
        plugin_dir_url(__FILE__) . 'assets/gfs-map.css',
        array('gfs-table'),
        GFS_VERSION
    );
    wp_register_script(
        'gfs-map',
        plugin_dir_url(__FILE__) . 'assets/gfs-map.js',
        array(),
        GFS_VERSION,
        true
    );
}

function gfs_register_settings() {
    register_setting(
        'gfs_settings',
        GFS_OPTION_BASE_URL,
        array(
            'type' => 'string',
            'sanitize_callback' => 'esc_url_raw',
            'default' => GFS_DEFAULT_BASE_URL,
        )
    );

    add_settings_section(
        'gfs_main_section',
        'Source des données nationales',
        '__return_false',
        'gfs-noaa'
    );

    add_settings_field(
        'gfs_data_base_url_field',
        'Adresse du dossier de données',
        'gfs_render_url_field',
        'gfs-noaa',
        'gfs_main_section'
    );
}

function gfs_render_url_field() {
    $value = get_option(GFS_OPTION_BASE_URL, GFS_DEFAULT_BASE_URL);
    printf(
        '<input type="url" class="regular-text code" name="%1$s" value="%2$s" autocomplete="off">',
        esc_attr(GFS_OPTION_BASE_URL),
        esc_attr($value)
    );
    echo '<p class="description">Conservez l’adresse proposée : elle pointe vers la branche nationale « data » du dépôt.</p>';
}

function gfs_add_settings_page() {
    add_options_page(
        'Tableau GFS / NOAA France',
        'GFS / NOAA',
        'manage_options',
        'gfs-noaa',
        'gfs_render_settings_page'
    );
}

function gfs_render_settings_page() {
    if (!current_user_can('manage_options')) {
        return;
    }
    ?>
    <div class="wrap">
        <h1>GFS / NOAA France</h1>
        <form action="options.php" method="post">
            <?php
            settings_fields('gfs_settings');
            do_settings_sections('gfs-noaa');
            submit_button();
            ?>
        </form>
        <p><strong>Version du module : <?php echo esc_html(GFS_VERSION); ?> (<?php echo esc_html(GFS_RELEASE_DATE); ?>)</strong></p>
        <h2>Shortcode unique</h2>
        <p><code>[gfs_meteo]</code> : cartes interactives, prévisions générales, orages, neige et graphiques.</p>
        <p><code>[gfs_meteo code="75056" departement="75" ville="Paris" heures="240"]</code></p>
        <p><code>[gfs_meteo code="66136" departement="66" ville="Perpignan" selecteur="non"]</code> : une seule ville, sans recherche.</p>
        <p>Le visiteur peut ensuite rechercher n’importe quelle commune ou saisir un code postal.</p>
    </div>
    <?php
}

function gfs_base_url() {
    $url = get_option(GFS_OPTION_BASE_URL, GFS_DEFAULT_BASE_URL);
    return untrailingslashit(apply_filters('gfs_national_data_base_url', $url));
}

function gfs_department_code($value) {
    $code = strtoupper(trim((string) $value));
    return preg_match('/^(?:\d{2}|2A|2B)$/', $code) ? $code : '66';
}

function gfs_commune_code($value) {
    $code = strtoupper(trim((string) $value));
    return preg_match('/^[0-9A-Z]{5}$/', $code) ? $code : '66136';
}

function gfs_unique_identifier() {
    if (function_exists('wp_unique_id')) {
        return wp_unique_id('gfs-city-');
    }
    return 'gfs-city-' . wp_rand(1000, 999999);
}

function gfs_map_variable($value) {
    $variable = strtolower(trim(sanitize_key((string) $value)));
    $allowed = array(
        'temperature',
        'temperature_ressentie',
        'point_rosee',
        'humidex',
        'pluie_1h',
        'pluie_cumul',
        'neige',
        'neige_au_sol',
        'equivalent_eau_neige',
        'graupel',
        'vent',
        'rafales',
        'rafales_max',
        'pression',
        'pression_surface',
        'nebulosite',
        'nuages_bas',
        'nuages_moyens',
        'nuages_eleves',
        'humidite',
        'mucape',
        'reflectivite',
        'altitude',
    );
    return in_array($variable, $allowed, true) ? $variable : 'temperature';
}

function gfs_render_map_shortcode($atts) {
    $atts = shortcode_atts(
        array(
            'variable' => 'temperature',
            'hauteur' => '900',
            'titre' => 'Cartes GFS France',
            'animation' => 'oui',
        ),
        $atts,
        'gfs_meteo'
    );

    $variable = gfs_map_variable($atts['variable']);
    $height = max(440, min(1100, absint($atts['hauteur'])));
    $title = trim(sanitize_text_field($atts['titre']));
    if ($title === '') {
        $title = 'Cartes GFS France';
    }
    $animation_value = strtolower(trim(sanitize_text_field($atts['animation'])));
    $animation = !in_array($animation_value, array('non', '0', 'false', 'off'), true);
    $map_id = function_exists('wp_unique_id')
        ? wp_unique_id('gfs-map-')
        : 'gfs-map-' . wp_rand(1000, 999999);

    wp_enqueue_style('gfs-map');
    wp_enqueue_script('gfs-map');

    ob_start();
    ?>
    <section
        id="<?php echo esc_attr($map_id); ?>"
        class="gfs-card gfsm-card"
        data-gfsm-app
        data-base-url="<?php echo esc_url(gfs_base_url()); ?>"
        data-variable="<?php echo esc_attr($variable); ?>"
        data-timezone="<?php echo esc_attr(wp_timezone_string()); ?>"
        data-animation="<?php echo $animation ? '1' : '0'; ?>"
        data-module-version="<?php echo esc_attr(GFS_VERSION); ?>"
        style="--gfsm-height: <?php echo esc_attr($height); ?>px"
    >
        <header class="gfs-header gfsm-header">
            <div>
                <p class="gfs-kicker">MODÈLE DÉTERMINISTE • JUSQU’À +240 H</p>
                <h2><?php echo esc_html($title); ?></h2>
                <p class="gfs-meta" data-gfsm-run>Chargement du dernier run GFS…</p>
            </div>
            <div class="gfs-badge"><span>GFS</span><strong>0,25°</strong></div>
        </header>

        <div class="gfsm-toolbar">
            <div class="gfsm-field gfsm-layer-picker">
                <span>Paramètre</span>
                <button
                    type="button"
                    class="gfsm-layer-trigger"
                    data-gfsm-menu-toggle
                    aria-expanded="false"
                    aria-controls="<?php echo esc_attr($map_id . '-layers'); ?>"
                >
                    <span data-gfsm-current-layer>Température à 2 m</span>
                </button>
            </div>
            <div class="gfsm-tools" aria-label="Outils de la carte">
                <button
                    type="button"
                    class="gfsm-tool-toggle"
                    data-gfsm-tool="capture"
                    aria-pressed="false"
                    title="Afficher les outils de capture et de copie"
                >📷 Outil capture</button>
                <button
                    type="button"
                    class="gfsm-tool-toggle"
                    data-gfsm-tool="diagram"
                    aria-pressed="false"
                    title="Cliquer sur la carte pour afficher le diagramme d’un point"
                >📈 Diagramme</button>
            </div>
            <div class="gfsm-time-controls" aria-label="Navigation dans les échéances">
                <button type="button" data-gfsm-previous title="Échéance précédente" aria-label="Échéance précédente">◀</button>
                <button type="button" data-gfsm-play title="Lancer l’animation" aria-label="Lancer l’animation">▶</button>
                <button type="button" data-gfsm-next title="Échéance suivante" aria-label="Échéance suivante">▶</button>
            </div>
            <div class="gfsm-validity-actions">
                <button
                    type="button"
                    class="gfsm-menu-close"
                    data-gfsm-menu-close
                    aria-label="Déplier le menu des cartes"
                    aria-expanded="false"
                    aria-controls="<?php echo esc_attr($map_id . '-layers'); ?>"
                >
                    <span data-gfsm-menu-label>Déplier</span><span class="gfsm-menu-close-icon" data-gfsm-menu-icon aria-hidden="true">⌄</span>
                </button>
                <div class="gfsm-validity">
                    <span>Prévision valable</span>
                    <strong data-gfsm-validity>—</strong>
                    <small data-gfsm-lead>—</small>
                </div>
            </div>
        </div>

        <p class="gfsm-tool-hint" data-gfsm-tool-hint hidden></p>

        <div
            id="<?php echo esc_attr($map_id . '-layers'); ?>"
            class="gfsm-layer-menu"
            data-gfsm-layer-menu
            hidden
        >
            <div class="gfsm-layer-menu-head">
                <div>
                    <strong>Choisir une carte GFS</strong>
                    <small>Paramètres disponibles dans la production publique NOAA GFS</small>
                </div>
                <label class="gfsm-secondary-toggle">
                    <input type="checkbox" data-gfsm-secondary-toggle>
                    <span>Afficher les paramètres secondaires</span>
                </label>
            </div>
            <div class="gfsm-layer-grid" data-gfsm-layer-grid></div>
        </div>

        <div class="gfsm-period-selector" data-gfsm-period hidden>
            <div class="gfsm-period-head">
                <div>
                    <strong data-gfsm-period-title>Période personnalisée</strong>
                    <small>Déplacez les deux curseurs pour choisir précisément le début et la fin.</small>
                </div>
                <span data-gfsm-period-summary>—</span>
            </div>
            <div class="gfsm-dual-range" data-gfsm-dual-range>
                <div class="gfsm-dual-range-track" aria-hidden="true"></div>
                <input data-gfsm-period-start type="range" min="0" max="1" value="0" step="1" aria-label="Début de la période">
                <input data-gfsm-period-end type="range" min="0" max="1" value="1" step="1" aria-label="Fin de la période">
            </div>
            <div class="gfsm-period-values">
                <span><small>Du</small><strong data-gfsm-period-start-label>—</strong></span>
                <span><small>Au</small><strong data-gfsm-period-end-label>—</strong></span>
            </div>
        </div>

        <p class="gfs-stale" data-gfsm-stale role="status" hidden>
            Attention : la dernière production disponible a plus de 8 heures.
        </p>

        <div class="gfsm-viewport" data-gfsm-viewport role="img" aria-label="Carte météo GFS interactive">
            <div class="gfsm-scene" data-gfsm-scene>
                <canvas class="gfsm-weather-canvas" data-gfsm-weather aria-hidden="true"></canvas>
                <canvas class="gfsm-vector-canvas" data-gfsm-vectors aria-hidden="true"></canvas>
            </div>
            <canvas class="gfsm-label-canvas" data-gfsm-labels aria-hidden="true"></canvas>
            <div class="gfsm-probe" data-gfsm-probe hidden>
                <strong data-gfsm-probe-value>—</strong>
                <span data-gfsm-probe-label>Valeur GFS</span>
            </div>
            <div class="gfsm-map-titlebar">
                <strong data-gfsm-map-title>Carte GFS</strong>
                <span data-gfsm-map-run>Run GFS —</span>
            </div>
            <div class="gfsm-map-date" data-gfsm-map-date>Échéance —</div>
            <div class="gfsm-map-buttons" aria-label="Commandes de zoom">
                <span class="gfsm-zoom-level" data-gfsm-zoom-level>100 %</span>
                <button type="button" data-gfsm-zoom-in title="Agrandir" aria-label="Agrandir">+</button>
                <button type="button" data-gfsm-zoom-out title="Réduire" aria-label="Réduire">−</button>
                <button type="button" data-gfsm-reset title="Recentrer" aria-label="Recentrer">⌂</button>
                <button type="button" data-gfsm-fullscreen title="Plein écran" aria-label="Plein écran">⛶</button>
            </div>
            <div class="gfsm-advanced-tools" data-gfsm-advanced-tools hidden aria-label="Outils avancés">
                <button type="button" data-gfsm-copy title="Copier la carte pour la coller dans un message ou un document" aria-label="Copier la carte dans le presse-papiers">📋 Copier l’image</button>
                <button type="button" data-gfsm-capture title="Télécharger la carte au format PNG" aria-label="Télécharger la carte au format PNG">📷 Télécharger PNG</button>
            </div>
            <div class="gfsm-diagram-popup" data-gfsm-diagram-popup hidden>
                <header>
                    <strong data-gfsm-diagram-title>—</strong>
                    <button type="button" data-gfsm-diagram-close aria-label="Fermer le diagramme">×</button>
                </header>
                <div class="gfsm-diagram-body" data-gfsm-diagram-body>
                    <p class="gfsm-diagram-status" data-gfsm-diagram-status>Chargement…</p>
                </div>
            </div>
            <div class="gfsm-legend" data-gfsm-legend aria-label="Légende de la carte"></div>
            <a class="gfsm-map-brand" href="https://www.alertes-meteo.com/" target="_blank" rel="noopener noreferrer">
                www.alertes-meteo.com • Module v<?php echo esc_html(GFS_VERSION); ?> (<?php echo esc_html(GFS_RELEASE_DATE); ?>)
            </a>
            <div class="gfsm-loading" data-gfsm-loading role="status">Chargement de la carte…</div>
            <div class="gfsm-error" data-gfsm-error role="alert" hidden></div>
        </div>

        <div class="gfsm-timeline" data-gfsm-timeline>
            <div data-gfsm-single-timeline>
                <input data-gfsm-slider type="range" min="0" max="0" value="0" step="1" aria-label="Échéance de prévision">
                <div class="gfsm-timeline-labels"><span>Run</span><span>Échéance maximale</span></div>
            </div>
        </div>

        <footer class="gfs-footer">
            <span data-gfsm-generated>Mise à jour en cours de lecture…</span>
            <span>
                Données météo directes :
                <a href="https://www.ncei.noaa.gov/products/weather-climate-models/global-forecast" target="_blank" rel="noopener noreferrer">GFS 0,25° — NOAA / NCEP</a>
                • <a href="https://www.alertes-meteo.com/" target="_blank" rel="noopener noreferrer">www.alertes-meteo.com</a>
                • Module cartes v<?php echo esc_html(GFS_VERSION); ?> (<?php echo esc_html(GFS_RELEASE_DATE); ?>)
            </span>
        </footer>

        <noscript>
            <p class="gfs-message gfs-error">JavaScript doit être activé pour afficher les cartes.</p>
        </noscript>
    </section>
    <?php
    return ob_get_clean();
}

function gfs_render_shortcode($atts) {
    $atts = shortcode_atts(
        array(
            'ville' => 'Perpignan',
            'code' => '66136',
            'departement' => '66',
            'heures' => '240',
            'titre' => '',
            'selecteur' => 'oui',
        ),
        $atts,
        'gfs_meteo'
    );

    $hours = max(3, min(240, absint($atts['heures'])));
    $city_name = sanitize_text_field($atts['ville']);
    if ($city_name === '') {
        $city_name = 'Perpignan';
    }
    $city_code = gfs_commune_code($atts['code']);
    $department = gfs_department_code($atts['departement']);
    $title_prefix = trim(sanitize_text_field($atts['titre']));
    if ($title_prefix === '') {
        $title_prefix = 'Prévisions GFS';
    }
    $selector_value = strtolower(trim(sanitize_text_field($atts['selecteur'])));
    $show_selector = !in_array($selector_value, array('non', '0', 'false', 'off'), true);

    $input_id = gfs_unique_identifier();
    $results_id = $input_id . '-results';
    $status_id = $input_id . '-status';

    wp_enqueue_style('gfs-table');
    wp_enqueue_script('gfs-table');
    wp_enqueue_style('gfs-map');
    wp_enqueue_script('gfs-map');

    ob_start();
    ?>
    <section
        class="gfs-card gfs-national"
        data-gfs-app
        data-base-url="<?php echo esc_url(gfs_base_url()); ?>"
        data-default-code="<?php echo esc_attr($city_code); ?>"
        data-default-department="<?php echo esc_attr($department); ?>"
        data-default-name="<?php echo esc_attr($city_name); ?>"
        data-hours="<?php echo esc_attr($hours); ?>"
        data-timezone="<?php echo esc_attr(wp_timezone_string()); ?>"
        data-title-prefix="<?php echo esc_attr($title_prefix); ?>"
        data-selector="<?php echo $show_selector ? '1' : '0'; ?>"
    >
        <header class="gfs-header">
            <div>
                <p class="gfs-kicker">MODÈLE DÉTERMINISTE • FRANCE MÉTROPOLITAINE</p>
                <h2 data-gfs-title><?php echo esc_html($title_prefix . ' — ' . $city_name); ?></h2>
                <div class="gfs-header-details">
                    <p class="gfs-city-altitude" data-gfs-altitude>Altitude de <?php echo esc_html($city_name); ?> : chargement…</p>
                    <p class="gfs-meta" data-gfs-meta>Chargement du dernier run GFS…</p>
                </div>
            </div>
            <div class="gfs-badge"><span>GFS</span><strong>0,25°</strong></div>
        </header>

        <div class="gfs-toolbar" <?php if (!$show_selector) : ?>hidden<?php endif; ?>>
            <div class="gfs-search">
                <div class="gfs-search-mainline">
                    <label for="<?php echo esc_attr($input_id); ?>">Choisissez votre commune</label>
                    <div class="gfs-search-control">
                        <span class="gfs-search-icon" aria-hidden="true">⌕</span>
                        <input
                            id="<?php echo esc_attr($input_id); ?>"
                            class="gfs-city-input"
                            type="search"
                            value="<?php echo esc_attr($city_name); ?>"
                            placeholder="Nom de commune ou code postal"
                            autocomplete="off"
                            spellcheck="false"
                            role="combobox"
                            aria-autocomplete="list"
                            aria-expanded="false"
                            aria-controls="<?php echo esc_attr($results_id); ?>"
                            aria-describedby="<?php echo esc_attr($status_id); ?>"
                        >
                    </div>
                    <button type="button" class="gfs-locate-button" data-gfs-locate>📍 Détecter ma ville</button>
                    <p
                        id="<?php echo esc_attr($status_id); ?>"
                        class="gfs-search-status"
                        role="status"
                        aria-live="polite"
                    >Saisissez au moins deux lettres ou un code postal.</p>
                </div>
                <div
                    id="<?php echo esc_attr($results_id); ?>"
                    class="gfs-search-results"
                    role="listbox"
                    hidden
                ></div>
            </div>
            <div class="gfs-coverage">
                <strong>34 746 communes</strong>
                <span>Métropole et Corse</span>
            </div>
        </div>

        <p class="gfs-stale" data-gfs-stale role="status" hidden>
            Attention : la dernière mise à jour disponible a plus de 8 heures.
        </p>

        <div class="gfs-tabs" role="tablist" aria-label="Type de prévision GFS">
            <button
                type="button"
                class="gfs-tab gfs-tab-map is-active"
                role="tab"
                aria-selected="true"
                data-gfs-tab="map"
            >🗺️ Cartes météo</button>
            <button
                type="button"
                class="gfs-tab"
                role="tab"
                aria-selected="false"
                data-gfs-tab="general"
            >🌤️ Prévisions générales</button>
            <button
                type="button"
                class="gfs-tab gfs-tab-storm"
                role="tab"
                aria-selected="false"
                data-gfs-tab="storms"
            >⛈️ Prévisions orages</button>
            <button
                type="button"
                class="gfs-tab gfs-tab-snow"
                role="tab"
                aria-selected="false"
                data-gfs-tab="snow"
            >❄️ Risque de neige</button>
        </div>

        <div class="gfs-panel gfs-map-panel" data-gfs-panel="map">
            <?php
            echo gfs_render_map_shortcode(
                array(
                    'variable' => 'temperature',
                    'hauteur' => '900',
                    'titre' => 'Cartes NOAA GFS — résolution 0,25°',
                    'animation' => 'oui',
                )
            );
            ?>
        </div>

        <div class="gfs-panel" data-gfs-panel="general" hidden>
            <div class="gfs-table-wrap gfs-general-wrap" role="region" aria-label="Prévisions générales par échéance" tabindex="0">
                <table class="gfs-table">
                    <thead>
                        <tr>
                            <th scope="col">Date</th>
                            <th scope="col">Heure</th>
                            <th scope="col">Temps</th>
                            <th scope="col">T°</th>
                            <th scope="col">Hum.</th>
                            <th scope="col">Pluie</th>
                            <th scope="col">Nuages</th>
                            <th scope="col">Vent</th>
                            <th scope="col">Rafales</th>
                            <th scope="col">Pression</th>
                        </tr>
                    </thead>
                    <tbody data-gfs-body-general>
                        <tr>
                            <td colspan="10" class="gfs-loading">Chargement des prévisions…</td>
                        </tr>
                    </tbody>
                </table>
            </div>

            <section class="gfs-charts" data-gfs-charts aria-label="Diagrammes GFS">
                <article class="gfs-chart-card">
                    <h3 data-gfs-chart-title-temperature>Diagramme températures (°C)</h3>
                    <div class="gfs-chart" data-gfs-chart-temperature></div>
                </article>
                <article class="gfs-chart-card">
                    <h3 data-gfs-chart-title-pressure>Diagramme pression ramenée au niveau de la mer (hPa)</h3>
                    <div class="gfs-chart" data-gfs-chart-pressure></div>
                </article>
                <article class="gfs-chart-card">
                    <h3 data-gfs-chart-title-rain>Diagramme précipitations (mm)</h3>
                    <p class="gfs-chart-total" data-gfs-rain-total>Précipitations cumulées : —</p>
                    <div class="gfs-chart" data-gfs-chart-rain></div>
                </article>
                <article class="gfs-chart-card">
                    <h3 data-gfs-chart-title-wind>Diagramme rafales et vent moyen</h3>
                    <div class="gfs-chart" data-gfs-chart-wind></div>
                </article>
            </section>
        </div>

        <div class="gfs-panel" data-gfs-panel="storms" hidden>
            <p class="gfs-storm-summary" data-gfs-storm-summary>
                Diagnostic convectif GFS : chargement…
            </p>
            <div class="gfs-top-scroll" data-gfs-top-scroll="storms" aria-label="Navigation horizontale du tableau orages" hidden><div></div></div>
            <div class="gfs-table-wrap gfs-storm-wrap" data-gfs-scroll-wrap="storms" role="region" aria-label="Prévisions d'orages par échéance" tabindex="0">
                <table class="gfs-table gfs-storm-table">
                    <thead>
                        <tr>
                            <th scope="col">Date</th>
                            <th scope="col">Heure</th>
                            <th scope="col">Risque orage</th>
                            <th scope="col">MUCAPE</th>
                            <th scope="col">Intensité pluie</th>
                            <th scope="col">LCL estimé</th>
                            <th scope="col">Foudre</th>
                            <th scope="col">Grêle</th>
                            <th scope="col">Pluie conv.</th>
                            <th scope="col">Graupel</th>
                            <th scope="col">Pluie / pas</th>
                            <th scope="col">Rafales</th>
                            <th scope="col">Type</th>
                            <th scope="col">Détails</th>
                        </tr>
                    </thead>
                    <tbody data-gfs-body-storms>
                        <tr>
                            <td colspan="14" class="gfs-loading">Chargement du diagnostic orageux…</td>
                        </tr>
                    </tbody>
                </table>
            </div>
            <p class="gfs-storm-note">
                <strong>Lecture expert :</strong> la CAPE et le taux de précipitation sont des sorties directes GFS. Le risque est un diagnostic indicatif qui exige à la fois de l’instabilité et un signal convectif actif ; la CAPE seule ne suffit jamais. La foudre, la grêle et le type sont des estimations dérivées clairement signalées.
            </p>
        </div>

        <div class="gfs-panel" data-gfs-panel="snow" hidden>
            <p class="gfs-snow-summary" data-gfs-snow-summary>
                Diagnostic neige GFS : chargement…
            </p>
            <div class="gfs-top-scroll" data-gfs-top-scroll="snow" aria-label="Navigation horizontale du tableau neige" hidden><div></div></div>
            <div class="gfs-table-wrap gfs-snow-wrap" data-gfs-scroll-wrap="snow" role="region" aria-label="Risque de neige par échéance" tabindex="0">
                <table class="gfs-table gfs-snow-table">
                    <thead>
                        <tr>
                            <th scope="col">Date</th>
                            <th scope="col">Heure</th>
                            <th scope="col">Risque neige</th>
                            <th scope="col">Phase</th>
                            <th scope="col">Neige / pas</th>
                            <th scope="col">Neige 3 h</th>
                            <th scope="col">Neige 6 h</th>
                            <th scope="col">Tenue</th>
                            <th scope="col">Pres. hPa</th>
                            <th scope="col">Hum.</th>
                            <th scope="col">Vent moy. / raf.</th>
                            <th scope="col">Cumul neige fraîche</th>
                            <th scope="col">Détails</th>
                        </tr>
                    </thead>
                    <tbody data-gfs-body-snow>
                        <tr>
                            <td colspan="13" class="gfs-loading">Chargement du risque de neige…</td>
                        </tr>
                    </tbody>
                </table>
            </div>
            <p class="gfs-snow-note">
                <strong>Lecture neige :</strong> la neige fraîche et la tenue sont estimées à partir de l’équivalent en eau du manteau neigeux GFS, de la température à 2 m et de l’altitude du point de grille.
            </p>
        </div>

        <footer class="gfs-footer">
            <span data-gfs-generated>Mise à jour en cours de lecture…</span>
            <span>
                Données météo directes :
                <a href="https://www.ncei.noaa.gov/products/weather-climate-models/global-forecast" target="_blank" rel="noopener noreferrer">GFS 0,25° — NOAA / NCEP</a>
                • Recherche des communes :
                <a href="https://geo.api.gouv.fr/decoupage-administratif/communes" target="_blank" rel="noopener noreferrer">API officielle française</a>
                • <a href="https://www.alertes-meteo.com/" target="_blank" rel="noopener noreferrer">www.alertes-meteo.com</a>
            </span>
            <span class="gfs-plugin-version">Module GFS v<?php echo esc_html(GFS_VERSION); ?> (<?php echo esc_html(GFS_RELEASE_DATE); ?>)</span>
        </footer>

        <noscript>
            <p class="gfs-message gfs-error">JavaScript doit être activé pour rechercher une commune.</p>
        </noscript>
    </section>
    <?php
    return ob_get_clean();
}
