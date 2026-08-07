use anyhow::{Context, Result};
use serde::Deserialize;
use serde_json::{json, Map, Value};
use std::env;
use std::fs;
use std::io::{self, Write};
use std::panic::{catch_unwind, AssertUnwindSafe};
use std::sync::Mutex;
use std::time::Instant;
pub use tig_challenges::__CHALLENGE_MODULE__::*;

__ALGORITHM_IMPORT__

const EVENT_PREFIX: &str = "TIG_EVENT\t";

#[derive(Deserialize)]
struct InstanceEnvelope {
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

fn optional_argument(args: &[String], name: &str) -> Option<String> {
    args.iter()
        .position(|value| value == name)
        .and_then(|position| args.get(position + 1))
        .cloned()
}

fn decode_seed(value: &str) -> Result<[u8; 32]> {
    if value.len() != 64 {
        anyhow::bail!("Algorithm seed must contain 64 hexadecimal characters");
    }
    let mut seed = [0_u8; 32];
    for (index, output) in seed.iter_mut().enumerate() {
        *output = u8::from_str_radix(&value[index * 2..index * 2 + 2], 16)
            .context("Algorithm seed is not valid hexadecimal")?;
    }
    Ok(seed)
}

fn emit(output: &Mutex<io::BufWriter<io::Stdout>>, event: Value) -> Result<()> {
    let mut output = output
        .lock()
        .map_err(|_| anyhow::anyhow!("Event output lock was poisoned"))?;
    writeln!(output, "{EVENT_PREFIX}{}", serde_json::to_string(&event)?)?;
    output.flush()?;
    Ok(())
}

fn run() -> Result<()> {
    let args = env::args().skip(1).collect::<Vec<_>>();
    let instance_path = argument(&args, "--instance")?;
    let seed = decode_seed(&argument(&args, "--algorithm-seed")?)?;
    let hyperparameters: Option<Map<String, Value>> = optional_argument(
        &args,
        "--hyperparameters-json",
    )
    .map(|value| {
        serde_json::from_str::<Map<String, Value>>(&value)
            .context("Hyperparameters must be a JSON object")
    })
    .transpose()?;

    let envelope: InstanceEnvelope = serde_json::from_slice(
        &fs::read(&instance_path)
            .with_context(|| format!("Could not read instance {instance_path}"))?,
    )?;
    let mut challenge: Challenge = serde_json::from_value(envelope.challenge)
        .context("Invalid canonical Challenge JSON")?;
    challenge.seed = seed;

    let output = Mutex::new(io::BufWriter::new(io::stdout()));
    emit(&output, json!({"type": "ready"}))?;
    let started = Instant::now();
    let save_solution = |solution: &Solution| -> Result<()> {
        let callback_wallclock_ms = started.elapsed().as_secs_f64() * 1000.0;
        emit(
            &output,
            json!({
                "type": "solution",
                "callback_wallclock_ms": callback_wallclock_ms,
                "solution": solution,
            }),
        )
    };

    let result = catch_unwind(AssertUnwindSafe(|| {
        algorithm::solve_challenge(&challenge, &save_solution, &hyperparameters)
    }));
    let solver_wallclock_ms = started.elapsed().as_secs_f64() * 1000.0;
    match result {
        Ok(Ok(())) => emit(
            &output,
            json!({
                "type": "complete",
                "outcome": "ok",
                "solver_wallclock_ms": solver_wallclock_ms,
            }),
        )?,
        Ok(Err(error)) => emit(
            &output,
            json!({
                "type": "complete",
                "outcome": "error",
                "solver_wallclock_ms": solver_wallclock_ms,
                "error": format!("{error:#}"),
            }),
        )?,
        Err(_) => emit(
            &output,
            json!({
                "type": "complete",
                "outcome": "panic",
                "solver_wallclock_ms": solver_wallclock_ms,
                "error": "Algorithm panicked",
            }),
        )?,
    }
    Ok(())
}

fn main() {
    if let Err(error) = run() {
        eprintln!("{error:#}");
        std::process::exit(1);
    }
}
