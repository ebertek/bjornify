//! Experimental, explicitly gated remote Spotify Connect queue probe.
//! Normal invocation is read-only. This program never transfers or starts playback.

use std::env;
use std::io::{self, IsTerminal, Read};
use std::time::Duration;

use librespot_core::{authentication::Credentials, config::SessionConfig, session::Session};
use reqwest::{Client, Method, StatusCode};
use serde_json::{json, Value};

const CONNECT_TIMEOUT: Duration = Duration::from_secs(20);
const REQUEST_TIMEOUT: Duration = Duration::from_secs(15);

enum Mode {
    AuthOnly,
    Inspect,
    Add {
        expected_device: String,
        track_uri: String,
    },
}

fn usage() -> ! {
    eprintln!(
        "Usage: connect-probe CLIENT_ID [inspect | add EXPECTED_DEVICE_ID spotify:track:ID --confirm-write]"
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
        _ => usage(),
    };
    (client_id.clone(), mode)
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

async fn add_one(session: &Session, device_id: &str, uri: &str) -> Result<(), &'static str> {
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
    tokio::time::timeout(
        REQUEST_TIMEOUT,
        session
            .spclient()
            .request(&Method::POST, &path, None, Some(body.as_bytes())),
    )
    .await
    .map_err(|_| "Connect-state queue command timed out")?
    .map_err(|_| "Connect-state queue command was rejected")?;
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
    let mut token = String::new();
    if io::stdin().read_to_string(&mut token).is_err() || token.trim().is_empty() {
        eprintln!("No Spotify OAuth token received on stdin.");
        std::process::exit(2);
    }
    let token = token.trim();

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
        Mode::Add {
            expected_device,
            track_uri,
        } => {
            let playback = match get_playback(token).await {
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
