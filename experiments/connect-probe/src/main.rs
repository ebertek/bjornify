//! Read-only Spotify Connect authorization experiment.
//! No player, queue writes or playback changes.

use librespot_core::{authentication::Credentials, config::SessionConfig, session::Session};

#[tokio::main]
async fn main() {
    println!("Experimental Spotify Connect session probe.");
}
