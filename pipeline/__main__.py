from .run import main

# The guard is load-bearing, not style: the validation pool uses the spawn
# start method, whose "safe importing of main module" contract re-imports
# this module in every worker — unguarded, main() would run in each child
# and kill the pool at bootstrap (plan §6).
if __name__ == "__main__":
    raise SystemExit(main())
