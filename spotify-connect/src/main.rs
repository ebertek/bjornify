//! Guarded Spotify Connect queue commands for Björnify.
//! Normal invocation is read-only. This program never transfers or starts playback.

use std::env;
use std::io::{self, IsTerminal, Read};
use std::net::Ipv4Addr;
use std::time::Duration;

use librespot_core::{
    authentication::Credentials, config::SessionConfig, error::Error as ConnectError,
    http_client::HttpClientError, session::Session, spclient::RequestStrategy,
};
use reqwest::{redirect::Policy, Client, Method, StatusCode};
use serde_json::{json, Value};

const CONNECT_TIMEOUT: Duration = Duration::from_secs(20);
const REQUEST_TIMEOUT: Duration = Duration::from_secs(15);
const SONOS_TIMEOUT: Duration = Duration::from_secs(5);
const SONOS_REPLY_MAX_BYTES: usize = 16 * 1024;

enum Mode {
    AuthOnly,
    Inspect,
    InspectDual,
    Diagnose,
    Add {
        expected_device: String,
        track_uri: String,
    },
    AddSonos {
        expected_name: String,
        address: Ipv4Addr,
        expected_device: String,
        track_uri: String,
    },
}

fn usage() -> ! {
    eprintln!(
        "Usage: bjornify-connect CLIENT_ID [inspect | inspect-dual | diagnose | add DEVICE_ID TRACK_URI --confirm-write | add-sonos NAME PRIVATE_IPV4 DEVICE_ID TRACK_URI --confirm-write]"
    );
    std::process::exit(2);
}

fn valid_track_uri(uri: &str) -> bool {
    let mut pieces = uri.split(':');
    matches!(
        (pieces.next(), pieces.next(), pieces.next(), pieces.next()),
        (Some("spotify"), Some("track"), Some(id), None)
            if id.len() == 22 && id.bytes().all(|c| c.is_ascii_alphanumeric())
    )
}

fn valid_device_id(id: &str) -> bool {
    (10..=128).contains(&id.len()) && id.bytes().all(|b| b.is_ascii_hexdigit())
}

fn sonos_private_ipv4(raw: &str) -> Option<Ipv4Addr> {
    raw.parse::<Ipv4Addr>()
        .ok()
        .filter(|address| address.is_private())
}

fn valid_device_name(name: &str) -> bool {
    !name.is_empty() && name.len() <= 80 && name.chars().all(|c| !c.is_control())
}

fn mode(args: &[String]) -> (String, Mode) {
    let Some(client_id) = args.get(1) else {
        usage()
    };
    if client_id.is_empty() {
        usage();
    }
    let mode = match args.get(2).map(String::as_str) {
        None if args.len() == 2 => Mode::AuthOnly,
        Some("inspect") if args.len() == 3 => Mode::Inspect,
        Some("inspect-dual") if args.len() == 3 => Mode::InspectDual,
        Some("diagnose") if args.len() == 3 => Mode::Diagnose,
        Some("add") if args.len() == 6 && args[5] == "--confirm-write" => {
            if !valid_device_id(&args[3]) || !valid_track_uri(&args[4]) {
                eprintln!("Invalid device ID or Spotify track URI; no command sent.");
                std::process::exit(2);
            }
            Mode::Add {
                expected_device: args[3].clone(),
                track_uri: args[4].clone(),
            }
        }
        Some("add-sonos") if args.len() == 8 && args[7] == "--confirm-write" => {
            let Some(address) = sonos_private_ipv4(&args[4]) else {
                usage()
            };
            if !valid_device_name(&args[3])
                || args[5].len() != 40
                || !valid_device_id(&args[5])
                || !valid_track_uri(&args[6])
            {
                eprintln!("Invalid Sonos target or track URI; no command sent.");
                std::process::exit(2);
            }
            Mode::AddSonos {
                expected_name: args[3].clone(),
                address,
                expected_device: args[5].clone(),
                track_uri: args[6].clone(),
            }
        }
        _ => usage(),
    };
    (client_id.clone(), mode)
}

/// Separate Connect/playback credentials from read-only Web API credentials.
/// Refuse any queue write without both independently supplied grants.
struct ProbeTokens {
    playback: String,
    web_api: Option<String>,
}

fn parse_tokens(raw: &str, mode: &Mode) -> Result<ProbeTokens, &'static str> {
    let raw = raw.trim();
    if raw.is_empty() || raw.len() > 16 * 1024 {
        return Err("No valid OAuth credentials received on stdin");
    }
    match mode {
        Mode::Add { .. } | Mode::AddSonos { .. } | Mode::InspectDual => {
            let data: Value =
                serde_json::from_str(raw).map_err(|_| "Separate OAuth grants required")?;
            let playback = data
                .get("playback_token")
                .and_then(Value::as_str)
                .filter(|value| valid_token(value))
                .ok_or("Playback OAuth grant missing or invalid")?;
            let web_api = data
                .get("web_api_token")
                .and_then(Value::as_str)
                .filter(|value| valid_token(value))
                .ok_or("Web API OAuth grant missing or invalid")?;
            if playback == web_api {
                return Err("Playback and Web API grants must be distinct");
            }
            Ok(ProbeTokens {
                playback: playback.to_owned(),
                web_api: Some(web_api.to_owned()),
            })
        }
        _ => {
            if raw.starts_with('{') || raw.starts_with('[') || !valid_token(raw) {
                return Err("Expected single access token for read-only mode");
            }
            Ok(ProbeTokens {
                playback: raw.to_owned(),
                web_api: None,
            })
        }
    }
}

fn valid_token(token: &str) -> bool {
    token.len() >= 8 && token.len() <= 8192 && !token.chars().any(char::is_whitespace)
}

struct Target {
    id: String,
    name: String,
    restricted: bool,
    active: bool,
}

fn parse_target(value: &Value) -> Option<Target> {
    let device = value.get("device")?;
    Some(Target {
        id: device.get("id")?.as_str()?.to_owned(),
        name: device.get("name")?.as_str()?.to_owned(),
        restricted: device.get("is_restricted")?.as_bool()?,
        active: device.get("is_active")?.as_bool()?,
    })
}

async fn get_playback(token: &str) -> Result<Option<Value>, &'static str> {
    let response = tokio::time::timeout(
        REQUEST_TIMEOUT,
        Client::new()
            .get("https://api.spotify.com/v1/me/player")
            .bearer_auth(token)
            .send(),
    )
    .await
    .map_err(|_| "Spotify playback lookup timed out")?
    .map_err(|_| "Spotify playback lookup failed")?;
    if response.status() == StatusCode::NO_CONTENT {
        return Ok(None);
    }
    if !response.status().is_success() {
        return Err("Spotify playback lookup returned an HTTP error");
    }
    let value: Value = response
        .json()
        .await
        .map_err(|_| "Spotify playback lookup returned invalid JSON")?;
    Ok(Some(value))
}

/// Print only selected playback fields, never raw API JSON, tokens, or account data.
fn playback_diagnostics(value: &Value) -> String {
    let playing = value
        .get("is_playing")
        .and_then(Value::as_bool)
        .map_or("unknown".to_string(), |b| b.to_string());

    let Some(device) = value.get("device").filter(|d| d.is_object()) else {
        return format!("PLAYBACK: playing={playing}; DEVICE: absent or null. READ ONLY.");
    };

    let name = device
        .get("name")
        .and_then(Value::as_str)
        .map(|name| {
            name.chars()
                .map(|c| if c.is_control() { ' ' } else { c })
                .collect::<String>()
        })
        .unwrap_or_else(|| "<missing>".to_string());
    let id = match device.get("id") {
        None => "<missing>".to_string(),
        Some(Value::Null) => "<null>".to_string(),
        Some(Value::String(id)) if valid_device_id(id) => id.clone(),
        _ => "<present but unusable>".to_string(),
    };
    let active = device
        .get("is_active")
        .and_then(Value::as_bool)
        .map_or("unknown".to_string(), |b| b.to_string());
    let restricted = device
        .get("is_restricted")
        .and_then(Value::as_bool)
        .map_or("unknown".to_string(), |b| b.to_string());
    let kind = device
        .get("type")
        .and_then(Value::as_str)
        .unwrap_or("<missing>");

    format!(
        "PLAYBACK: playing={playing}\nDEVICE: name={name}, type={kind}, id={id}, active={active}, restricted={restricted}\nREAD ONLY: no playback or queue commands sent."
    )
}

/// Validate the Sonos Connect device ID against the device itself.
async fn sonos_get_info(address: Ipv4Addr) -> Result<Value, &'static str> {
    if !address.is_private() {
        return Err("Sonos address must be on a private IPv4 LAN");
    }
    // This client intentionally does not use proxy settings or follow redirects.
    let client = Client::builder()
        .no_proxy()
        .redirect(Policy::none())
        .timeout(SONOS_TIMEOUT)
        .build()
        .map_err(|_| "Could not create Sonos LAN client")?;
    let url = format!("http://{address}:1400/spotifyzc?action=getInfo");
    let mut response = client
        .get(url)
        .send()
        .await
        .map_err(|_| "Sonos did not respond to ZeroConf getInfo")?;
    if response.status() != StatusCode::OK {
        return Err("Sonos ZeroConf getInfo returned an unexpected HTTP status");
    }
    let mut bytes = Vec::new();
    while let Some(chunk) = response
        .chunk()
        .await
        .map_err(|_| "Sonos ZeroConf response could not be read")?
    {
        if bytes.len().saturating_add(chunk.len()) > SONOS_REPLY_MAX_BYTES {
            return Err("Sonos ZeroConf reply exceeds size limit");
        }
        bytes.extend_from_slice(&chunk);
    }
    serde_json::from_slice(&bytes).map_err(|_| "Sonos ZeroConf returned invalid JSON")
}

/// Require independent Spotify Web API and Sonos ZeroConf evidence for the same target.
/// A missing Spotify ID is supported only when the user confirms an explicit LAN
/// address and device ID; never infer the identity from a room name alone.
fn sonos_identity_matches(
    playback: &Value,
    info: &Value,
    expected_name: &str,
    expected_device: &str,
) -> bool {
    let Some(device) = playback.get("device") else {
        return false;
    };
    let spotify_id = device.get("id");
    let spotify_id_matches = matches!(spotify_id, Some(Value::Null) | None)
        || spotify_id.and_then(Value::as_str) == Some(expected_device);

    playback.get("is_playing").and_then(Value::as_bool) == Some(true)
        && device.get("is_active").and_then(Value::as_bool) == Some(true)
        && device.get("is_restricted").and_then(Value::as_bool) == Some(true)
        && device.get("name").and_then(Value::as_str) == Some(expected_name)
        && device
            .get("type")
            .and_then(Value::as_str)
            .is_some_and(|kind| kind.eq_ignore_ascii_case("speaker"))
        && spotify_id_matches
        && info.get("deviceID").and_then(Value::as_str) == Some(expected_device)
        && info.get("remoteName").and_then(Value::as_str) == Some(expected_name)
        && info
            .get("deviceType")
            .and_then(Value::as_str)
            .is_some_and(|kind| kind.eq_ignore_ascii_case("speaker"))
}

/// Only expose a vetted error category and HTTP status. The underlying error
/// may contain Spotify service URLs, sensitive IDs, or response metadata.
fn safe_command_failure(error: &ConnectError) -> String {
    if let Some(HttpClientError::StatusCode(status)) = error.error.downcast_ref::<HttpClientError>()
    {
        let meaning = match status.as_u16() {
            400 => "invalid Connect-state request",
            401 => "Connect-state authentication rejected",
            403 => "Connect-state permission denied",
            404 => "Connect-state route or device not found",
            409 => "Connect-state conflict",
            429 => "Connect-state rate limited",
            500..=599 => "Connect-state service error",
            _ => "unexpected Connect-state HTTP response",
        };
        return format!("HTTP {} ({meaning})", status.as_u16());
    }
    format!("librespot error category: {:?}", error.kind)
}

/// Read-only with respect to playback and queue: requests the same Spotify
/// client/service tokens needed for a Connect command, without sending one.
/// Map login5's narrowly defined server rejection enum into diagnostic labels.
/// Never echo arbitrary error strings: they may contain account identifiers.
fn safe_login5_failure(error: &ConnectError) -> String {
    let detail = error.error.to_string();
    const REJECTIONS: [&str; 9] = [
        "UNKNOWN_ERROR",
        "INVALID_CREDENTIALS",
        "BAD_REQUEST",
        "UNSUPPORTED_LOGIN_PROTOCOL",
        "TIMEOUT",
        "UNKNOWN_IDENTIFIER",
        "TOO_MANY_ATTEMPTS",
        "INVALID_PHONENUMBER",
        "TRY_AGAIN_LATER",
    ];
    for rejection in REJECTIONS {
        if detail == format!("Login request was denied: {rejection}") {
            return format!("login5 denied stored credentials: {rejection}");
        }
    }
    if detail.starts_with("Couldn't successfully authenticate after ") {
        return "login5 exhausted authentication retries".to_string();
    }
    safe_command_failure(error)
}

async fn diagnose_service_auth(session: &Session) {
    println!("CONNECT LOGIN: PASS");
    println!(
        "REUSABLE CREDENTIALS: {}",
        if session.auth_data().is_empty() {
            "missing"
        } else {
            "present"
        }
    );

    let client_token =
        tokio::time::timeout(Duration::from_secs(25), session.spclient().client_token()).await;
    match client_token {
        Ok(Ok(_)) => println!("SPCLIENT CLIENT TOKEN: PASS"),
        Ok(Err(error)) => {
            println!(
                "SPCLIENT CLIENT TOKEN: FAIL ({})",
                safe_command_failure(&error)
            );
            println!("READ ONLY: no Connect-state queue commands sent.");
            return;
        }
        Err(_) => {
            println!("SPCLIENT CLIENT TOKEN: TIMEOUT");
            println!("READ ONLY: no Connect-state queue commands sent.");
            return;
        }
    }

    let service_token =
        tokio::time::timeout(Duration::from_secs(25), session.login5().auth_token()).await;
    match service_token {
        Ok(Ok(_)) => println!("LOGIN5 SERVICE TOKEN: PASS"),
        Ok(Err(error)) => {
            println!(
                "LOGIN5 SERVICE TOKEN: FAIL ({})",
                safe_login5_failure(&error)
            );
            println!("READ ONLY: no Connect-state queue commands sent.");
            return;
        }
        Err(_) => {
            println!("LOGIN5 SERVICE TOKEN: TIMEOUT");
            println!("READ ONLY: no Connect-state queue commands sent.");
            return;
        }
    }

    match tokio::time::timeout(REQUEST_TIMEOUT, session.spclient().base_url()).await {
        Ok(Ok(_)) => println!("SPCLIENT ENDPOINT RESOLUTION: PASS"),
        Ok(Err(error)) => println!(
            "SPCLIENT ENDPOINT RESOLUTION: FAIL ({})",
            safe_command_failure(&error)
        ),
        Err(_) => println!("SPCLIENT ENDPOINT RESOLUTION: TIMEOUT"),
    }
    println!("READ ONLY: no Connect-state queue commands sent.");
}

async fn add_one(session: &Session, device_id: &str, uri: &str) -> Result<(), String> {
    // This is Spotify's private Connect-state route, not its documented Web API.
    // It is based on the restricted-Sonos implementation in crmne/spotifast.
    let path = format!(
        "/connect-state/v1/player/command/from/{}/to/{device_id}",
        session.device_id()
    );
    let body = json!({
        "command": {"endpoint": "add_to_queue", "track": {"uri": uri}}
    })
    .to_string();
    // Keep a failed/ambiguous queue write from being automatically replayed.
    // Spotify's generic spclient normally retries unavailable endpoints.
    let client = session.spclient();
    client.set_strategy(RequestStrategy::TryTimes(1));
    tokio::time::timeout(
        REQUEST_TIMEOUT,
        client.request(&Method::POST, &path, None, Some(body.as_bytes())),
    )
    .await
    .map_err(|_| "Connect-state queue command timed out".to_string())?
    .map_err(|error| {
        format!(
            "Connect-state queue command failed: {}",
            safe_command_failure(&error)
        )
    })?;
    Ok(())
}

#[tokio::main]
async fn main() {
    let (client_id, command) = mode(&env::args().collect::<Vec<_>>());
    if io::stdin().is_terminal() {
        eprintln!(
            "OAuth token must be supplied through a pipe, never typed or passed as an argument."
        );
        std::process::exit(2);
    }
    let mut token_data = String::new();
    if io::stdin().read_to_string(&mut token_data).is_err() {
        eprintln!("Could not read OAuth credentials from stdin.");
        std::process::exit(2);
    }
    let tokens = match parse_tokens(&token_data, &command) {
        Ok(tokens) => tokens,
        Err(message) => {
            eprintln!("REFUSED: {message}. No Spotify playback or queue command sent.");
            std::process::exit(2);
        }
    };
    let token = tokens.playback.as_str();

    let mut config = SessionConfig::default();
    config.client_id = client_id;
    let session = Session::new(config, None);
    match tokio::time::timeout(
        CONNECT_TIMEOUT,
        session.connect(Credentials::with_access_token(token), false),
    )
    .await
    {
        Ok(Ok(())) => (),
        Ok(Err(_)) => {
            eprintln!("FAIL: Spotify rejected this OAuth grant for Connect login.");
            std::process::exit(1);
        }
        Err(_) => {
            eprintln!("FAIL: Spotify Connect login timed out.");
            std::process::exit(1);
        }
    }

    match command {
        Mode::AuthOnly => {
            println!("PASS: Spotify accepted a librespot session; no playback actions were sent.");
        }
        Mode::Inspect => match get_playback(token).await {
            Ok(Some(playback)) => println!("{}", playback_diagnostics(&playback)),
            Ok(None) => println!("NO PLAYBACK STATE: no playback or queue commands sent."),
            Err(msg) => {
                eprintln!("FAIL: {msg}. No playback or queue commands sent.");
                std::process::exit(1);
            }
        },
        Mode::Diagnose => diagnose_service_auth(&session).await,
        Mode::InspectDual => {
            let Some(web_api_token) = tokens.web_api.as_deref() else {
                eprintln!("REFUSED: Personal Web API grant missing.");
                std::process::exit(2);
            };
            match get_playback(web_api_token).await {
                Ok(Some(playback)) => println!("{}", playback_diagnostics(&playback)),
                Ok(None) => println!("NO PLAYBACK STATE: no playback or queue commands sent."),
                Err(msg) => {
                    eprintln!("FAIL: {msg}. No playback or queue commands sent.");
                    std::process::exit(1);
                }
            }
        }
        Mode::AddSonos {
            expected_name,
            address,
            expected_device,
            track_uri,
        } => {
            // GET only: prove the Connect identity using a caller-selected LAN
            // endpoint before checking what Spotify currently reports as active.
            let info = match sonos_get_info(address).await {
                Ok(info) => info,
                Err(message) => {
                    eprintln!("REFUSED: {message}. No queue command sent.");
                    std::process::exit(1);
                }
            };
            let web_api_token = tokens.web_api.as_deref().expect("Dual grant required");
            let playback = match get_playback(web_api_token).await {
                Ok(Some(value)) => value,
                _ => {
                    eprintln!("REFUSED: Could not verify Spotify's current playback.");
                    std::process::exit(1);
                }
            };
            if !sonos_identity_matches(&playback, &info, &expected_name, &expected_device) {
                eprintln!(
                    "REFUSED: Spotify's active restricted speaker does not match Sonos ZeroConf identity. No queue command sent."
                );
                std::process::exit(1);
            }
            match add_one(&session, &expected_device, &track_uri).await {
                Ok(()) => println!(
                    "SENT: one Connect-state queue command to {expected_name}. Verify the Spotify queue; delivery is not guaranteed."
                ),
                Err(message) => {
                    eprintln!("FAIL: {message}. Queue outcome unknown; do not retry blindly.");
                    std::process::exit(1);
                }
            }
        }
        Mode::Add {
            expected_device,
            track_uri,
        } => {
            let web_api_token = tokens.web_api.as_deref().expect("Dual grant required");
            let playback = match get_playback(web_api_token).await {
                Ok(Some(value))
                    if value.get("is_playing").and_then(Value::as_bool) == Some(true) =>
                {
                    value
                }
                _ => {
                    eprintln!("REFUSED: Cannot verify that Spotify is currently playing.");
                    std::process::exit(1);
                }
            };
            let Some(current) = parse_target(&playback) else {
                eprintln!("REFUSED: Spotify did not provide a complete active device identity.");
                std::process::exit(1);
            };
            if !valid_device_id(&current.id)
                || current.id != expected_device
                || !current.restricted
                || !current.active
            {
                eprintln!(
                    "REFUSED: Active device must match the confirmed ID and be active + restricted. No queue command sent."
                );
                std::process::exit(1);
            }
            match add_one(&session, &current.id, &track_uri).await {
                Ok(()) => println!(
                    "SENT: one Connect-state queue command to {}. Check Spotify's queue to verify it appeared.",
                    current.name
                ),
                Err(msg) => {
                    eprintln!("FAIL: {msg}. The queue outcome is unconfirmed.");
                    std::process::exit(1);
                }
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn queue_write_refuses_single_token_or_duplicate_grants() {
        let mode = Mode::AddSonos {
            expected_name: "Test speaker".to_string(),
            address: "192.168.1.10".parse().unwrap(),
            expected_device: "0123456789abcdef0123456789abcdef01234567".to_string(),
            track_uri: "spotify:track:4iV5W9uYEdYUVa79Axb7Rh".to_string(),
        };
        assert!(parse_tokens("singleaccesstoken123", &mode).is_err());
        assert!(parse_tokens(
            r#"{"playback_token":"the-same-token","web_api_token":"the-same-token"}"#,
            &mode
        )
        .is_err());
        assert!(parse_tokens(r#"{"playback_token":"valid-playback-token"}"#, &mode).is_err());
        assert!(parse_tokens(r#"{"playback_token":"valid-playback-token","web_api_token":"different-web-api-token"}"#, &mode).is_ok());
        assert!(parse_tokens(r#"{"playback_token":"valid-playback-token","web_api_token":"different-web-api-token"}"#, &Mode::InspectDual).is_ok());
        assert!(parse_tokens("valid-playback-token", &Mode::Diagnose).is_ok());
        assert!(parse_tokens(
            r#"{"playback_token":"valid-playback-token"}"#,
            &Mode::Diagnose
        )
        .is_err());
    }

    #[test]
    fn login5_rejection_is_whitelisted_and_never_leaks_service_errors() {
        let denied =
            ConnectError::failed_precondition("Login request was denied: INVALID_CREDENTIALS");
        assert_eq!(
            safe_login5_failure(&denied),
            "login5 denied stored credentials: INVALID_CREDENTIALS"
        );
        let denied = ConnectError::failed_precondition("Login request was denied: BAD_REQUEST");
        assert_eq!(
            safe_login5_failure(&denied),
            "login5 denied stored credentials: BAD_REQUEST"
        );
        let unknown =
            ConnectError::failed_precondition("Login request was denied: SECRET_TOKEN_123");
        assert_eq!(
            safe_login5_failure(&unknown),
            "librespot error category: FailedPrecondition"
        );
        assert!(!safe_login5_failure(&unknown).contains("SECRET_TOKEN_123"));
    }

    #[test]
    fn diagnose_mode_is_read_only_and_has_no_track_or_device_arguments() {
        let (client, command) = mode(&[
            "probe".to_string(),
            "test-client-id".to_string(),
            "diagnose".to_string(),
        ]);
        assert_eq!(client, "test-client-id");
        assert!(matches!(command, Mode::Diagnose));
    }

    #[test]
    fn queue_errors_reveal_http_status_without_including_private_message() {
        let error = ConnectError::new(
            librespot_core::error::ErrorKind::PermissionDenied,
            HttpClientError::StatusCode(StatusCode::FORBIDDEN),
        );
        assert_eq!(
            safe_command_failure(&error),
            "HTTP 403 (Connect-state permission denied)"
        );
        let error = ConnectError::new(
            librespot_core::error::ErrorKind::NotFound,
            HttpClientError::StatusCode(StatusCode::NOT_FOUND),
        );
        assert_eq!(
            safe_command_failure(&error),
            "HTTP 404 (Connect-state route or device not found)"
        );
        let error = ConnectError::failed_precondition(
            "credentials must not appear in diagnostics: secret-canary",
        );
        let output = safe_command_failure(&error);
        assert!(output.contains("FailedPrecondition"));
        assert!(!output.contains("secret-canary"));
    }

    #[test]
    fn sonos_write_requires_matching_active_restricted_device_and_receiver() {
        let playback = json!({
            "is_playing": true,
            "device": {
                "name": "Test speaker",
                "type": "Speaker",
                "id": null,
                "is_active": true,
                "is_restricted": true
            }
        });
        let info = json!({
            "remoteName": "Test speaker",
            "deviceType": "SPEAKER",
            "deviceID": "0123456789abcdef0123456789abcdef01234567"
        });
        let id = "0123456789abcdef0123456789abcdef01234567";
        assert!(sonos_identity_matches(&playback, &info, "Test speaker", id));
        assert!(!sonos_identity_matches(&playback, &info, "Other room", id));
        let mut changed = playback.clone();
        changed["device"]["is_restricted"] = json!(false);
        assert!(!sonos_identity_matches(&changed, &info, "Test speaker", id));
        changed = playback.clone();
        changed["is_playing"] = json!(false);
        assert!(!sonos_identity_matches(&changed, &info, "Test speaker", id));
        changed = playback.clone();
        changed["device"]["id"] = json!("fedcba9876543210fedcba9876543210fedcba98");
        assert!(!sonos_identity_matches(&changed, &info, "Test speaker", id));
        let mut invalid_info = info.clone();
        invalid_info["deviceID"] = json!("fedcba9876543210fedcba9876543210fedcba98");
        assert!(!sonos_identity_matches(
            &playback,
            &invalid_info,
            "Test speaker",
            id
        ));
        invalid_info = info.clone();
        invalid_info["remoteName"] = json!("Other room");
        assert!(!sonos_identity_matches(
            &playback,
            &invalid_info,
            "Test speaker",
            id
        ));
    }

    #[test]
    fn sonos_write_accepts_only_private_ipv4_and_safe_names() {
        assert_eq!(
            sonos_private_ipv4("192.168.2.47"),
            Some("192.168.2.47".parse().unwrap())
        );
        for address in ["8.8.8.8", "127.0.0.1", "169.254.1.2", "::1", "example.com"] {
            assert!(sonos_private_ipv4(address).is_none());
        }
        assert!(valid_device_name("Test speaker"));
        assert!(!valid_device_name(""));
        assert!(!valid_device_name("Sunroom\nInjected"));
    }

    #[test]
    fn only_single_track_uris_are_accepted() {
        assert!(valid_track_uri("spotify:track:4iV5W9uYEdYUVa79Axb7Rh"));
        assert!(!valid_track_uri("spotify:album:4iV5W9uYEdYUVa79Axb7Rh"));
        assert!(!valid_track_uri("spotify:track:bad"));
        assert!(!valid_track_uri(
            "spotify:track:4iV5W9uYEdYUVa79Axb7Rh:extra"
        ));
    }

    #[test]
    fn inspection_reports_missing_fields_without_inventing_device_identity() {
        let diag = playback_diagnostics(&json!({
            "is_playing": true,
            "device": {
                "id": null,
                "name": "Family room",
                "type": "speaker",
                "is_active": true
            }
        }));
        assert!(diag.contains("playing=true"));
        assert!(diag.contains("id=<null>"));
        assert!(diag.contains("restricted=unknown"));
        assert!(diag.contains("READ ONLY"));
        assert!(parse_target(&json!({
            "device": {
                "id": null,
                "name": "Family room",
                "is_active": true,
                "is_restricted": true
            }
        }))
        .is_none());
    }

    #[test]
    fn inspection_handles_missing_device_and_strips_control_characters() {
        assert!(
            playback_diagnostics(&json!({"is_playing": false})).contains("DEVICE: absent or null")
        );
        let diag = playback_diagnostics(&json!({
            "device": {
                "id": "0123456789abcdef",
                "name": "Room\nInjected"
            }
        }));
        assert!(!diag.contains("\nInjected"));
    }

    #[test]
    fn a_device_must_have_an_explicit_identity_and_restriction_flag() {
        assert!(
            parse_target(&json!({
                "device": {
                    "id": "0123456789abcdef", "name": "Family room",
                    "is_restricted": true, "is_active": true
                }
            }))
            .unwrap()
            .restricted
        );
        assert!(parse_target(&json!({"device": {
            "id": "0123456789abcdef", "name": "Family room"
        }}))
        .is_none());
        assert!(!valid_device_id("id/with/path"));
    }
}
