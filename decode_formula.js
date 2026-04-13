// Decode the ika-tools bundle string constants
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
    console.log('Bundle size:', source.length);

    // Extract and execute JUST the string functions in a sandbox
    // Get the xt() function definition
    const xtFuncMatch = source.match(/(function xt\(\)\{const r=\[[\s\S]*?;return xt=function\(\)\{return r\},xt\(\)\})/);

    // Get the rotation + pt definition
    const rotAndPtMatch = source.match(/(\(function\(r,i\)\{const l=pt,o=r\(\);for\(;;\)try\{[\s\S]*?\}\)\(xt,906067\);function pt\(r,i\)\{const l=xt\(\);return pt=function\(o,s\)\{return o=o-\d+,l\[o\]\},pt\(r,i\)\})/);

    if (!xtFuncMatch || !rotAndPtMatch) {
        console.error('Could not extract functions');
        // Try alternate approach: find the actual pt function text
        const ptIdx = source.indexOf('function pt(r,i){const l=xt()');
        if (ptIdx >= 0) {
            console.log('Found pt at', ptIdx);
            console.log(source.substring(ptIdx, ptIdx+200));
        }
        return;
    }

    // Execute in a vm sandbox
    const sandbox = {};
    const code = xtFuncMatch[1] + '\n' + rotAndPtMatch[1];

    try {
        vm.runInNewContext(code, sandbox);
        console.log('Executed rotation code');
    } catch(e) {
        console.error('Error:', e.message);
    }

    // pt is now defined in sandbox — but we need to extract it
    // Alternative: extract manually

    // Find offset
    const offsetMatch = source.match(/return pt=function\(o,s\)\{return o=o-(\d+),l\[o\]\}/);
    if (!offsetMatch) {
        console.error('Could not find offset');
        return;
    }
    const offset = parseInt(offsetMatch[1]);
    console.log('Offset:', offset);

    // Extract xt() array
    const xtArrMatch = source.match(/function xt\(\)\{const r=(\[[\s\S]*?\]);return xt/);
    const xtArr = eval(xtArrMatch[1]);
    console.log('Array length:', xtArr.length);

    // Find the rotation condition
    const condMatch = source.match(/for\(;;\)try\{if\((-?parseInt\(l\(\d+\)\).*?)===i\)break;o\.push\(o\.shift\(\)\)\}catch\{o\.push\(o\.shift\(\)\)\}\}\)\(xt,906067\)/);
    if (!condMatch) {
        console.error('No condition found');
        // Show what's near 906067
        const idx = source.indexOf('906067');
        console.log(source.substring(idx-500, idx+20));
        return;
    }

    const condExpr = condMatch[1];
    console.log('Condition:', condExpr.substring(0, 150));

    // Simulate rotation
    let arr = [...xtArr];
    let found = false;
    for (let attempt = 0; attempt < arr.length + 5; attempt++) {
        const l = (n) => arr[n - offset];
        try {
            // Replace l(N) with parseInt(arr[N-offset])
            const evaled = eval(condExpr.replace(/l\((\d+)\)/g, (_, n) => {
                const s = l(parseInt(n));
                return `parseInt(${JSON.stringify(s)})`;
            }));
            if (evaled === 906067) {
                console.log(`✓ Correct rotation after ${attempt} shifts`);
                found = true;
                break;
            }
        } catch(e) {
            // continue
        }
        arr.push(arr.shift());
    }

    if (!found) {
        console.log('Warning: rotation condition not met, using last rotation state');
    }

    const F = (n) => arr[n - offset];

    // Print all meaningful strings
    console.log('\n=== All decoded strings ===');
    for (let n = offset; n < offset + arr.length; n++) {
        const val = F(n);
        console.log(`F(${n}) = ${JSON.stringify(val)}`);
    }

    // Now decode the formula
    console.log('\n=== Critical formula variables ===');
    // From the row rendering code with these specific indices:
    const formula_indices = {
        180: 'column name (building_time display column)',
        181: 'innerHTML property name',
        188: 'construction_time_reduction field',
        190: 'capacity field',
        199: 'value property',
        200: 'ul selector (chronos input)',
        204: 'chronos_forge slug',
        206: 'split method',
        209: 'glas field',
        221: 'duration field',
        241: 'level field',
        243: 'querySelectorAll method',
        244: 'change event',
        248: 'wood field',
        251: 'time.day unit',
        265: 'setAttribute method',
        266: 'factor element selector',
        284: 'querySelector method',
        285: 'add method',
        308: 'Math.round method',
        309: 'bn selector (building time input)',
        313: 'construction_time_reduction selector',
    };

    for (const [n, desc] of Object.entries(formula_indices)) {
        console.log(`F(${n}) = ${JSON.stringify(F(parseInt(n)))} // ${desc}`);
    }

    // Specifically for the bn (building time input):
    console.log('\n=== Building time input ===');
    const bnSelector = F(309);
    console.log(`bn selector: ${bnSelector}`);
    console.log('This is the element whose .value is used as se in the formula');
}

main().catch(console.error);
