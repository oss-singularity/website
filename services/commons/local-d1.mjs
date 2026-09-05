import { DatabaseSync } from 'node:sqlite';
import { readFileSync, readdirSync } from 'node:fs';

// Local test/development adapter only. The production worker uses its D1 binding.
// This executes actual SQLite SQL and transactions without third-party packages.
export class SQLiteD1 {
  constructor(path = ':memory:') {
    this.sqlite = new DatabaseSync(path);
    this.sqlite.exec('PRAGMA foreign_keys = ON');
    // Local bookkeeping is separate from Cloudflare's migration history. Apply
    // each additive migration once; restarting never reseeds removed content.
    this.sqlite.exec('CREATE TABLE IF NOT EXISTS local_migrations (name TEXT PRIMARY KEY, applied_at INTEGER NOT NULL)');
    const directory = new URL('./migrations/', import.meta.url);
    for (const name of readdirSync(directory).filter(name => /^\d{4}_[a-z0-9_]+\.sql$/.test(name)).sort()) {
      if (this.sqlite.prepare('SELECT name FROM local_migrations WHERE name = ?').get(name)) continue;
      this.sqlite.exec('BEGIN IMMEDIATE');
      try {
        this.sqlite.exec(readFileSync(new URL(name, directory), 'utf8'));
        this.sqlite.prepare('INSERT INTO local_migrations (name, applied_at) VALUES (?, ?)').run(name, Date.now());
        this.sqlite.exec('COMMIT');
      } catch (error) {
        this.sqlite.exec('ROLLBACK');
        this.sqlite.close();
        throw error;
      }
    }
  }
  prepare(sql) {
    const owner = this;
    return {
      sql, values: [],
      bind(...values) { return { ...this, values }; },
      async first() { return owner.sqlite.prepare(this.sql).get(...this.values) ?? null; },
      async all() { return owner.execute(this); },
    };
  }
  execute(statement) {
    const results = this.sqlite.prepare(statement.sql).all(...statement.values);
    const changes = this.sqlite.prepare('SELECT changes() AS count').get().count;
    return { success: true, results, meta: { changes } };
  }
  async batch(statements) {
    this.sqlite.exec('BEGIN IMMEDIATE');
    try {
      const results = statements.map((statement) => this.execute(statement));
      this.sqlite.exec('COMMIT');
      return results;
    } catch (error) {
      this.sqlite.exec('ROLLBACK');
      throw error;
    }
  }
}
