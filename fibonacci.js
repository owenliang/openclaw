let a = 0;
let b = 1;
const result = [];

while (a <= 100) {
    result.push(a);
    let next = a + b;
    a = b;
    b = next;
}

console.log("100以内的斐波那契数列：");
console.log(result.join(', '));