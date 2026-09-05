import { DatabaseSync } from 'node:sqlite';
import { readFileSync } from 'node:fs';

// Local test/development adapter only. The production worker uses its D1 binding.
// This executes actual SQLite SQL and transactions without third-party packages.
export class SQLiteD1 {
  constructor(path = ':memory:') {
    this.sqlite = new DatabaseSync(path);
    this.sqlite.exec(readFileSync(new URL('./migrations/0001_commons.sql', import.meta.url), 'utf8'));
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
