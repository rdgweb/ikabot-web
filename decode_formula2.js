// Decode ika-tools formula using proper JS evaluation
const https = require('https');
const vm = require('vm');

function fetch(url) {
    return new Promise((resolve, reject) => {
        https.get(url, (res) => {
            let data = '';
            res.on('data', chunk => data += chunk);
            res.on('end', () => resolve(data));
            res.on('error', reject);
        }).on('error', reject);
    });
}

async function main() {
    const home = await fetch('https://ika-tools.com/');
    const bundleMatch = home.match(/src="\.\/(assets\/index-[^"]+\.js)"/);
    const bundleUrl = 'https://ika-tools.com/' + bundleMatch[1];
    const source = await fetch(bundleUrl);

    // Strategy: Extract just the xt() function and rotation code, then execute

    // Find xt() function using our extraction algorithm
    const xtStart = source.indexOf('function xt(){const r=[');
    if (xtStart < 0) { console.error('No xt found'); return; }

    // Find the closing ] using proper string tracking
    const contentStart = xtStart + 'function xt(){const r=['.length;
    let depth = 1, inStr = false, q = '', escaped = false;
    let i = contentStart;
    while (i < source.length && depth > 0) {
        const c = source[i];
        if (inStr) {
            if (escaped) escaped = false;
            else if (c === '\\') escaped = true;
            else if (c === q) inStr = false;
        } else {
            if (c === '"' || c === "'") { inStr = true; q = c; }
            else if (c === '[') depth++;
            else if (c === ']') depth--;
        }
        i++;
    }
    const arrayText = source.substring(xtStart, i) + '\nreturn r; }';

    // Evaluate the array extraction
    const sandbox = {};
    vm.runInNewContext('function xt() {' + 'const r=' + source.substring(contentStart, i-1) + ';\nreturn r;\n}', sandbox);

    // Wait, easier approach: just evaluate the whole xt function
    const xtFn = new Function('return ' + source.substring(xtStart, source.indexOf('return xt=function(){return r},xt()}')+35));
    // That's too complex. Let me try differently.

    // Extract the array literal with proper string-aware extraction
    let arrContent = source.substring(contentStart, i-1);
    // Use Node.js to safely evaluate
    const arr = vm.runInNewContext('[' + arrContent + ']');
    console.log('Array length:', arr.length);
    console.log('First few:', arr.slice(0, 5));
    console.log('Last few:', arr.slice(-5));

    // Find offset
    const offsetMatch = source.match(/return pt=function\(o,s\)\{return o=o-(\d+),l\[o\]\}/);
    const offset = parseInt(offsetMatch[1]);
    console.log('Offset:', offset);

    // Find rotation condition
    const idx906 = source.indexOf('906067');
    // Find the for loop condition
    const condStart = source.lastIndexOf('if(', idx906);
    const condEnd = source.indexOf(')break', condStart);
    const condExpr = source.substring(condStart + 3, condEnd);
    console.log('Condition length:', condExpr.length);
    console.log('Condition:', condExpr.substring(0, 200));

    // Parse l(N) indices from condition
    const lIndices = [...condExpr.matchAll(/l\((\d+)\)/g)].map(m => parseInt(m[1]));
    console.log('l() indices in condition:', lIndices);
    console.log('Max index:', Math.max(...lIndices), '-> array index:', Math.max(...lIndices) - offset);
    console.log('Array length:', arr.length, '(max valid index:', arr.length-1, ')');

    // Simulate rotation
    let rotArr = [...arr];
    let found = false;
    for (let attempt = 0; attempt <= rotArr.length; attempt++) {
        const l = (n) => {
            const idx = n - offset;
            return idx >= 0 && idx < rotArr.length ? rotArr[idx] : '0';
        };
        try {
            const result = eval(condExpr.replace(/l\((\d+)\)/g, (_, n) => {
                const s = l(parseInt(n));
                return JSON.stringify(s);
            }).replace(/parseInt\(("[^"]*")\)/g, (_, s) => {
                return String(parseInt(eval(s)));
            }));
            if (result === 906067) {
                console.log(`✓ Correct rotation after ${attempt} shifts`);
                found = true;
                break;
            }
        } catch(e) {
            // continue
        }
        rotArr.push(rotArr.shift());
    }

    if (!found) console.log('Warning: rotation not found');

    const F = (n) => {
        const idx = n - offset;
        return idx >= 0 && idx < rotArr.length ? rotArr[idx] : undefined;
    };

    // Print key formula variables
    console.log('\n=== Decoded Formula Variables ===');

    // Re-decode the formula with correct mappings
    // From the row rendering code: using the OUTER scope 'r' alias -> w alias -> $ alias -> all = F
    // Key mappings to find:
    const toFind = [
        [241, 'level column'],
        [180, 'duration column (triggers building_time branch)'],
        [240, 'value property'],
        [247, 'split method'],
        [310, 'includes method'],
        [233, 'round method'],
        [235, 'value property of input'],
        [236, 'ul = chronos input selector'],
        [248, 'selectGroupBuildingTime label selector'],
        [224, 'construction_time_reduction field'],
        [245, 'chronos_forge slug'],
        [279, 'querySelector method'],
        [217, 'innerHTML property'],
    ];

    for (const [n, desc] of toFind) {
        console.log(`F(${n}) = ${JSON.stringify(F(n))} // ${desc}`);
    }

    // Print ALL
    console.log('\n=== All strings ===');
    for (let n = offset; n < offset + rotArr.length; n++) {
        console.log(`F(${n}) = ${JSON.stringify(F(n))}`);
    }
}

main().catch(console.error);
