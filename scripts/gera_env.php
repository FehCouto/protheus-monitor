<?php
// Gera o .env do protheus-monitor a partir do config do QuerysBD, sem exibir segredos.
// Uso (no Windows):  php gera_env.php
// Reexecutar preserva as senhas ja geradas; so atualiza os dados do banco.

$destino = __DIR__ . '/../.env';
$cfg = require 'C:/QuerysBD/config/database.php';
$prod = $cfg['producao'] ?? null;
if (!$prod) {
    fwrite(STDERR, "Config 'producao' nao encontrado em C:/QuerysBD/config/database.php\n");
    exit(1);
}
if (strpbrk($prod['password'], "'\n\r") !== false) {
    fwrite(STDERR, "A senha do banco contem aspas simples ou quebra de linha; ajuste o .env manualmente.\n");
    exit(1);
}

$atual = [];
if (is_file($destino)) {
    foreach (file($destino, FILE_IGNORE_NEW_LINES) as $linha) {
        if (preg_match("/^([A-Z_]+)='?(.*?)'?$/", $linha, $m)) {
            $atual[$m[1]] = $m[2];
        }
    }
}
$senha = fn(string $k, int $n = 24) => $atual[$k] ?? substr(bin2hex(random_bytes(32)), 0, $n);

$env = [
    'TZ'                     => $atual['TZ'] ?? 'America/Sao_Paulo',
    'ELASTIC_VERSION'        => '9.5.4',
    'ZABBIX_VERSION'         => $atual['ZABBIX_VERSION'] ?? 'alpine-7.0-latest',
    'ELASTIC_PASSWORD'       => $senha('ELASTIC_PASSWORD'),
    'KIBANA_SYSTEM_PASSWORD' => $senha('KIBANA_SYSTEM_PASSWORD'),
    'KIBANA_ENCRYPTION_KEY'  => $senha('KIBANA_ENCRYPTION_KEY', 40),
    'ZABBIX_DB_PASSWORD'     => $senha('ZABBIX_DB_PASSWORD'),
    'ZABBIX_ADMIN_PASSWORD'  => $senha('ZABBIX_ADMIN_PASSWORD', 16),
    'MSSQL_HOST'             => $prod['host'],
    'MSSQL_PORT'             => $prod['port'],
    'MSSQL_DATABASE'         => $prod['database'],
    'MSSQL_USER'             => $prod['username'],
    'MSSQL_PASSWORD'         => $prod['password'],
    'PROTHEUS_REST_HOST'     => $atual['PROTHEUS_REST_HOST'] ?? 'SEU-AMBIENTE.protheus.cloudtotvs.com.br',
    'PROTHEUS_REST_PORT'     => $atual['PROTHEUS_REST_PORT'] ?? '4050',
    // Usuario do Protheus dedicado ao monitoramento: preencher a mao no .env (sem aspas simples na senha).
    'PROTHEUS_REST_USER'     => $atual['PROTHEUS_REST_USER'] ?? '',
    'PROTHEUS_REST_PASSWORD' => $atual['PROTHEUS_REST_PASSWORD'] ?? '',
];

$linhas = ["# Gerado por scripts/gera_env.php - NAO versionar, NAO compartilhar."];
foreach ($env as $k => $v) {
    $linhas[] = "{$k}='{$v}'";
}
file_put_contents($destino, implode("\n", $linhas) . "\n");
echo "OK: .env gravado com " . count($env) . " variaveis (segredos nao exibidos).\n";
