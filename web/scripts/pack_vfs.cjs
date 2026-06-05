const fs = require('fs');
const path = require('path');

const ROOT_DIR = path.resolve(__dirname, '../../');
const OUTPUT_FILE = path.join(ROOT_DIR, 'web/public/vfs.json');

const TARGETS = [
  { dir: 'python/emu', dest: 'emu' },
  { dir: 'python/reference', dest: 'reference' },
  { file: 'python/harness.py', dest: 'harness.py' },
  { dir: 'data', dest: 'data', extensions: ['.csv'] }
];

const vfs = {};

function walkSync(currentDirPath, destPrefix, extensions) {
  fs.readdirSync(currentDirPath).forEach((name) => {
    const filePath = path.join(currentDirPath, name);
    const stat = fs.statSync(filePath);
    
    if (name === '__pycache__') return;
    
    if (stat.isFile()) {
      if (extensions && !extensions.includes(path.extname(name))) return;
      if (!extensions && path.extname(name) !== '.py') return; // Default to python files
      
      const destPath = path.join(destPrefix, name).replace(/\\\\/g, '/');
      console.log(`Packing ${destPath}`);
      vfs[destPath] = fs.readFileSync(filePath, 'utf-8');
    } else if (stat.isDirectory()) {
      walkSync(filePath, path.join(destPrefix, name), extensions);
    }
  });
}

console.log('Building Pyodide VFS...');

TARGETS.forEach(target => {
  const fullPath = path.join(ROOT_DIR, target.dir || target.file);
  const stat = fs.statSync(fullPath);
  
  if (stat.isFile()) {
    console.log(`Packing ${target.dest}`);
    vfs[target.dest] = fs.readFileSync(fullPath, 'utf-8');
  } else if (stat.isDirectory()) {
    walkSync(fullPath, target.dest, target.extensions);
  }
});

fs.writeFileSync(OUTPUT_FILE, JSON.stringify(vfs, null, 2));
console.log(`VFS packed to ${OUTPUT_FILE} with ${Object.keys(vfs).length} files.`);
