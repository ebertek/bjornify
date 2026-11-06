//! Read-only Spotify Connect authentication proof of concept.
//! No receiver, player, queue writes, or playback changes are created.

use std::env;
use std::io::{self, IsTerminal, Read};
use std::time::Duration;

use librespot_core::{
    authentication::Credentials, config::SessionConfig, session::Session,
};

#[tokio::main]
async fn main() {
    let client_id = match (env::args().nth(1), env::args().nth(2)) {
        (Some(id), None) if !id.is_empty() => id,
        _ => {
            eprintln!("Usage: bjornify-connect-auth-probe SPOTIFY_CLIENT_ID");
            std::process::exit(2);
        }
    };
    if io::stdin().is_terminal() {
        eprintln!("A token must be provided through a pipe, not typed.");
        std::process::exit(2);
    }

    let mut token = String::new();
    if io::stdin().read_to_string(&mut token).is_err() || token.trim().is_empty() {
        eprintln!("No Spotify OAuth token received on stdin.");
        std::process::exit(2);
    }

    let mut config = SessionConfig::default();
    config.client_id = client_id;
    let session = Session::new(config, None);
    let credentials = Credentials::with_access_token(token.trim());
    let result = tokio::time::timeout(
        Duration::from_secs(20),
        session.connect(credentials, false),
    )
    .await;

    match result {
        Ok(Ok(())) => println!(
            "PASS: Spotify accepted a librespot session; no playback actions were sent."
        ),
        Ok(Err(_)) => {
            eprintln!("FAIL: Spotify rejected this OAuth grant for Connect session login.");
            std::process::exit(1);
        }
        Err(_) => {
            eprintln!("FAIL: Spotify login timed out; no playback actions were sent.");
            std::process::exit(1);
        }
    }
}
