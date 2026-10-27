use anyhow::{Context, Result};
use serde::Deserialize;
use serde_json::{json, Value};
use std::env;
use std::fs;
use std::io::{self, Write};
use std::time::Instant;
use tig_challenges::__CHALLENGE_MODULE__::{Challenge, Solution, Track};
use tig_structs::core::BenchmarkSettings;

const CHALLENGE_ID: &str = "__CHALLENGE_ID__";
const OBJECTIVE_DIRECTION: &str = "__OBJECTIVE_DIRECTION__";
const EVENT_PREFIX: &str = "TIG_EVENT\t";

#[derive(Deserialize)]
struct InstanceEnvelope {
    challenge_id: String,
    challenge: Value,
}

fn argument(args: &[String], name: &str) -> Result<String> {
    let position = args
        .iter()
        .position(|value| value == name)
        .with_context(|| format!("Missing required argument {name}"))?;
    args.get(position + 1)
        .cloned()
        .with_context(|| format!("Missing value for argument {name}"))
}

fn parse_track(value: &str) -> Result<Track> {
    let encoded = if value.starts_with('"') && value.ends_with('"') {
        value.to_owned()
    } else {
        serde_json::to_string(value)?
    };
    serde_json::from_str(&encoded)
        .with_context(|| format!("Invalid {CHALLENGE_ID} track {value:?}"))
}

fn load_instance(path: &str) -> Result<Challenge> {
    let envelope: InstanceEnvelope = serde_json::from_slice(
        &fs::read(path).with_context(|| format!("Could not read instance {path}"))?,
    )?;
    if envelope.challenge_id != CHALLENGE_ID {
        anyhow::bail!(
            "Instance challenge {} does not match helper {}",
            envelope.challenge_id,
            CHALLENGE_ID
        );
    }
    serde_json::from_value(envelope.challenge).context("Invalid canonical Challenge JSON")
}

fn raw_objective(challenge: &Challenge, solution: &Solution) -> Result<Value> {
    __OBJECTIVE_EXPRESSION__
}

fn baseline_solution(challenge: &Challenge) -> Result<Solution> {
    __BASELINE_EXPRESSION__
}

fn prepare_challenge(challenge: Challenge) -> Result<Challenge> {
    __PREPARE_EXPRESSION__
}

fn generate(args: &[String]) -> Result<Value> {
    let nonce: u64 = argument(args, "--nonce")?.parse()?;
    let track = parse_track(&argument(args, "--track")?)?;
    let seed = argument(args, "--seed")?;
    let track_id = match serde_json::to_value(&track)? {
        Value::String(value) => value,
        _ => anyhow::bail!("Canonical Track did not serialize as a string"),
    };
    let settings = BenchmarkSettings {
        // Part of the canonical seed derivation; it keeps generated datasets
        // stable and is intentionally unchanged by the repository rename.
        player_id: "tig-challenges".to_owned(),
        block_id: "offline-v1".to_owned(),
        challenge_id: CHALLENGE_ID.to_owned(),
        algorithm_id: "offline".to_owned(),
        track_id,
    };
    let challenge_seed = settings.calc_seed(&seed, nonce);
    let challenge = Challenge::generate_instance(&challenge_seed, &track)?;
    Ok(json!({
        "challenge": challenge,
        "generation": {
            "seed": seed,
            "nonce": nonce,
            "challenge_seed": challenge_seed.iter().map(|byte| format!("{byte:02x}")).collect::<String>(),
            "benchmark_settings": settings
        }
    }))
}

fn evaluate(args: &[String]) -> Result<Value> {
    let challenge = load_instance(&argument(args, "--instance")?)?;
    let solution: Solution = serde_json::from_slice(
        &fs::read(argument(args, "--solution")?).context("Could not read solution")?,
    )
    .context("Invalid canonical Solution JSON")?;
    Ok(json!({
        "objective": raw_objective(&challenge, &solution)?,
        "direction": OBJECTIVE_DIRECTION
    }))
}

fn prepare(args: &[String]) -> Result<Value> {
    let challenge = load_instance(&argument(args, "--instance")?)?;
    Ok(json!({"challenge": prepare_challenge(challenge)?}))
}

fn baseline(args: &[String]) -> Result<Value> {
    let challenge = load_instance(&argument(args, "--instance")?)?;
    let started = Instant::now();
    let solution = baseline_solution(&challenge)?;
    let solver_wallclock_ms = started.elapsed().as_secs_f64() * 1000.0;
    let objective = raw_objective(&challenge, &solution)?;
    Ok(json!({
        "solution": solution,
        "objective": objective,
        "direction": OBJECTIVE_DIRECTION,
        "solver_wallclock_ms": solver_wallclock_ms
    }))
}

fn emit_event(value: Value) -> Result<()> {
    let stdout = io::stdout();
    let mut output = stdout.lock();
    writeln!(output, "{EVENT_PREFIX}{}", serde_json::to_string(&value)?)?;
    output.flush()?;
    Ok(())
}

fn baseline_events(args: &[String]) -> Result<()> {
    let challenge = load_instance(&argument(args, "--instance")?)?;
    emit_event(json!({"type": "ready"}))?;
    let started = Instant::now();
    match baseline_solution(&challenge) {
        Ok(solution) => {
            let solver_wallclock_ms = started.elapsed().as_secs_f64() * 1000.0;
            emit_event(json!({
                "type": "solution",
                "callback_wallclock_ms": solver_wallclock_ms,
                "solution": solution
            }))?;
            emit_event(json!({
                "type": "complete",
                "outcome": "ok",
                "solver_wallclock_ms": solver_wallclock_ms
            }))?;
        }
        Err(error) => {
            emit_event(json!({
                "type": "complete",
                "outcome": "error",
                "solver_wallclock_ms": started.elapsed().as_secs_f64() * 1000.0,
                "error": format!("{error:#}")
            }))?;
        }
    }
    Ok(())
}

fn main() {
    let args = env::args().collect::<Vec<_>>();
    if args.get(1).map(String::as_str) == Some("baseline-events") {
        if let Err(error) = baseline_events(&args[2..]) {
            eprintln!("{error:#}");
            std::process::exit(1);
        }
        return;
    }
    let result = match args.get(1).map(String::as_str) {
        Some("generate") => generate(&args[2..]),
        Some("prepare") => prepare(&args[2..]),
        Some("evaluate") => evaluate(&args[2..]),
        Some("baseline") => baseline(&args[2..]),
        Some(command) => Err(anyhow::anyhow!("Unknown command {command:?}")),
        None => Err(anyhow::anyhow!(
            "Expected generate, prepare, evaluate, baseline, or baseline-events command"
        )),
    };

    match result {
        Ok(value) => println!("{}", value),
        Err(error) => {
            eprintln!("{error:#}");
            std::process::exit(1);
        }
    }
}
