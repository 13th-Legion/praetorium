<?php
// Tesserarius nag bot helper: dump per-user last Talk activity as TSV (username<TAB>unix_ts).
// Read via: docker exec -i nextcloud-app php < talk_activity.php
include "/var/www/html/config/config.php";
$pdo = new PDO("mysql:host=" . $CONFIG["dbhost"] . ";dbname=" . $CONFIG["dbname"], $CONFIG["dbuser"], $CONFIG["dbpassword"]);
$q = $pdo->query(
    "SELECT actor_id, MAX(last_attendee_activity) AS last_active " .
    "FROM oc_talk_attendees WHERE actor_type='users' AND last_attendee_activity > 0 " .
    "GROUP BY actor_id"
);
foreach ($q as $r) {
    echo $r["actor_id"], "	", $r["last_active"], "
";
}
