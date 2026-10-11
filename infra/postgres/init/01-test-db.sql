-- Runs once when the Postgres volume is first created.
-- A separate database keeps the test suite from touching demo data.
CREATE DATABASE datarescue_test OWNER datarescue;
-- Benchmarks reset this database on every run.
CREATE DATABASE datarescue_bench OWNER datarescue;
