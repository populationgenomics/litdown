// Reads a MathML string from stdin, writes LaTeX to stdout.
// Exit code 1 on conversion error.
const { MathMLToLaTeX } = require('mathml-to-latex');
const chunks = [];
process.stdin.setEncoding('utf8');
process.stdin.on('data', d => chunks.push(d));
process.stdin.on('end', () => {
    try {
        process.stdout.write(MathMLToLaTeX.convert(chunks.join('').trim()));
    } catch (e) {
        process.stderr.write(e.message + '\n');
        process.exit(1);
    }
});
