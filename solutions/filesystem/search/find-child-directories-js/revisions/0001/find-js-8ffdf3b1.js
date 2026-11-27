"use strict";

const fs = require("node:fs");
const path = require("node:path");

/**
 * Find immediate child directories matching one literal name without recursion.
 * The caller supplies an authorized root and a single nonempty child name; matching
 * ignores case and returns sorted absolute paths. Roots or ancestors that are
 * symbolic links or junctions are rejected, and child links are skipped. This
 * reads directory metadata only. Invalid input or filesystem errors are thrown;
 * callers must handle them and must not interpret an error as a complete search.
 */
function findChildDirectories(root, name) {
    if (typeof root !== "string" || !root || typeof name !== "string" || !name ||
        name === "." || name === ".." || /[\\/\0]/.test(name)) {
        throw new TypeError("root and a single child directory name are required");
    }
    const absolute = path.resolve(root);
    let component = path.parse(absolute).root;
    const components = absolute.slice(component.length).split(path.sep).filter(Boolean);
    for (const part of [null, ...components]) {
        if (part !== null) component = path.join(component, part);
        const info = fs.lstatSync(component);
        if (info.isSymbolicLink() || !info.isDirectory()) {
            throw new Error("root and its ancestors must be ordinary directories");
        }
    }
    return fs.readdirSync(absolute, { withFileTypes: true })
        .filter(entry => entry.isDirectory() && !entry.isSymbolicLink() &&
                         entry.name.toLowerCase() === name.toLowerCase())
        .map(entry => path.join(absolute, entry.name))
        .sort();
}

module.exports = { findChildDirectories };
