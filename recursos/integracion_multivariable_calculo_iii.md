# Cálculo III: integración en varias variables


## 1. La integral doble como acumulación

En una variable, una integral definida acumula los valores de una función a lo largo de un intervalo. En dos variables, una función f(x,y) asigna un valor a cada punto de una región D del plano. La integral doble ∫∫_D f(x,y) dA acumula esos valores sobre toda la región. Puede definirse dividiendo D en celdas pequeñas de área ΔA, eligiendo un punto de cada celda y tomando el límite de las sumas f(xᵢ,yᵢ)ΔA cuando las celdas se refinan.

La interpretación depende de la función. Si f=1, la integral doble es el área de D. Si f es no negativa y representa la altura de una superficie, la integral es el volumen bajo la superficie z=f(x,y) y sobre D. Si f es una densidad superficial, medida por ejemplo en kg/m², la integral da la masa de una lámina. Si f toma valores positivos y negativos, la integral acumula con signo; para hallar un volumen geométrico hay que identificar cuál superficie es superior y restarle la inferior.

La linealidad permite integrar una suma término a término y sacar una constante fuera de la integral. También podemos dividir D en regiones sin superposición interior y sumar sus integrales. Esta última propiedad sirve cuando una frontera cambia de forma y no existe una descripción simple de toda la región. Antes de calcular, conviene dibujar D, indicar sus unidades y decidir qué cantidad física representa f.

Por ejemplo, en el rectángulo 0≤x≤2 y 0≤y≤3, la integral de 1 vale 6. Si f=x+y, los valores crecen al moverse a la derecha o hacia arriba. Por eso su integral debe ser positiva y su promedio debe estar entre 0 y 5. Más adelante veremos que la integral vale 15 y que el promedio es 15/6=2,5.

No hay que confundir la función que se integra con la región de integración. Dos integrales con el mismo f pueden tener valores distintos si cambia D. Del mismo modo, una región puede servir para estudiar volumen, masa o promedio según el integrando elegido.

## 2. Integrales iteradas y teorema de Fubini

Una integral doble sobre un rectángulo puede calcularse mediante dos integrales ordinarias sucesivas. Si D=[a,b]×[c,d] y f es continua, el teorema de Fubini permite escribir ∫∫_D f dA como ∫_a^b[∫_c^d f(x,y)dy]dx o como ∫_c^d[∫_a^b f(x,y)dx]dy. Se resuelve primero la integral interior. Mientras se integra respecto de y, x permanece constante; al terminar, el resultado se integra respecto de x.

Para f(x,y)=x+y en 0≤x≤2, 0≤y≤3, la integral interior en y es ∫_0^3(x+y)dy=3x+9/2. La integral exterior es ∫_0^2(3x+9/2)dx=15. Si invertimos el orden, ∫_0^2(x+y)dx=2+2y y luego ∫_0^3(2+2y)dy=15. El resultado coincide, aunque el trabajo puede ser más sencillo en uno de los dos órdenes.

Si f(x,y)=g(x)h(y) y D es un rectángulo, la integral se separa en el producto de ∫g(x)dx y ∫h(y)dy con sus límites correspondientes. Esta regla no debe aplicarse mecánicamente a un triángulo: allí el intervalo disponible para una variable depende de la otra. La forma del dominio es tan importante como la forma del integrando.

En la práctica elegimos un orden que simplifique los límites o la primitiva interior. En una región no rectangular, cambiar el orden significa describir de nuevo cada corte de la región; intercambiar solo los símbolos dx y dy suele dar una integral distinta. Cuando hay singularidades o integrales impropias, también hay que comprobar las hipótesis que justifican el intercambio.

Como ejercicio, calcula ∫∫_D xy dA para D=[0,1]×[0,2]. Con dy en el interior resulta ∫_0^1 x[∫_0^2 y dy]dx=∫_0^1 2x dx=1. Con dx en el interior se obtiene lo mismo. La simetría del cálculo confirma que Fubini se está usando sobre el mismo rectángulo.

## 3. Regiones de tipo I y de tipo II

Muchas regiones tienen fronteras curvas. Una región de tipo I se describe mediante cortes verticales: a≤x≤b y g₁(x)≤y≤g₂(x). Su integral doble se escribe ∫_a^b∫_{g₁(x)}^{g₂(x)}f(x,y)dy dx. Una región de tipo II se describe mediante cortes horizontales: c≤y≤d y h₁(y)≤x≤h₂(y). En ese caso dx aparece en la integral interior. Una misma región puede admitir las dos descripciones.

Considera el triángulo limitado por y=0, y=x y x=1. Con cortes verticales, 0≤x≤1 y 0≤y≤x. Con cortes horizontales, 0≤y≤1 y y≤x≤1. Por tanto, ∫_0^1∫_0^x f(x,y)dy dx y ∫_0^1∫_y^1 f(x,y)dx dy representan la misma integral doble. Si f=1, ambas devuelven el área del triángulo, que es 1/2.

El cambio de orden se hace dibujando la región, encontrando los puntos de intersección de las curvas y trazando un corte en la nueva dirección. Después se leen sus extremos. Por ejemplo, la región entre y=x² e y=x, para 0≤x≤1, tiene descripción vertical x²≤y≤x. Al despejar x, la descripción horizontal es y≤x≤√y para 0≤y≤1. Su área es ∫_0^1(x−x²)dx=1/6; también es ∫_0^1(√y−y)dy=1/6.

Hay que revisar las ramas al despejar una frontera. La ecuación x=y² implica y=√x o y=−√x; elegir una sin mirar el dibujo puede omitir la mitad de D. Una región con estrechamientos puede requerir dividirse en dos o más partes. Es preferible sumar integrales simples que escribir límites que cambian de significado a mitad del recorrido.

Los cortes verticales y horizontales también ayudan a interpretar el área. Cuando f=1, integrar primero respecto de y suma longitudes verticales g₂(x)−g₁(x); integrar primero respecto de x suma longitudes horizontales h₂(y)−h₁(y). Ambas estrategias acumulan la misma región.

## 4. Coordenadas polares

Las coordenadas polares describen un punto del plano mediante su distancia r al origen y su ángulo θ: x=r cosθ, y=r sinθ, con r≥0. Son útiles para discos, coronas y sectores circulares. Además, la expresión x²+y² se transforma en r², lo que puede simplificar mucho el integrando. Sin embargo, no basta con reemplazar x e y: también cambia el elemento de área.

Un sector pequeño de ángulo Δθ y grosor Δr tiene longitud aproximada rΔθ y área aproximada rΔrΔθ. En el límite se obtiene dA=r dr dθ. El factor r es el jacobiano de la transformación polar. Si se olvida, la integral no mide el área correcta. Para el disco de radio R se usan 0≤r≤R y 0≤θ≤2π. La integral de 1 es ∫_0^{2π}∫_0^R r dr dθ=πR².

Para el disco unitario, calculemos ∫∫_D(x²+y²)dA. El integrando se vuelve r² y, junto al elemento de área, queda r³ dr dθ. Por tanto, ∫_0^{2π}∫_0^1 r³ dr dθ=π/2. El resultado es menor que el área π del disco, lo cual tiene sentido porque 0≤x²+y²≤1 dentro de D.

Una corona entre radios a y b usa a≤r≤b. Un sector de apertura α restringe θ a un intervalo de longitud α. En una semicircunferencia superior, por ejemplo, θ puede variar entre 0 y π. Estas descripciones deben cubrir la región una sola vez, salvo las fronteras que tienen área nula.

La curva r=2cosθ ilustra por qué hay que mirar el signo de r. En cartesianas equivale a x²+y²=2x, una circunferencia de centro (1,0) y radio 1. Para describir su interior con r≥0 se puede usar −π/2≤θ≤π/2 y 0≤r≤2cosθ. Tomar todos los ángulos entre 0 y 2π con ese límite radial introduciría valores negativos.

Polares no siempre conviene. Para un rectángulo alejado del origen, los límites polares pueden ser complicados. La decisión debe tener en cuenta tanto la forma del dominio como la función que se integra.

## 5. Cambio de variables y determinante jacobiano

Las coordenadas polares son un caso particular de cambio de variables. Supongamos que una transformación T expresa x=x(u,v) e y=y(u,v). Si es suficientemente regular y cubre la región de interés una sola vez, podemos integrar en una región S del plano uv. La fórmula usa dA=|J_T(u,v)|du dv, donde J_T es el determinante de la matriz de derivadas parciales de x e y respecto de u y v.

El jacobiano mide el factor local de escala del área. Se toma su valor absoluto porque invertir la orientación cambia el signo del determinante, pero no el área geométrica. En polares, el determinante de x=r cosθ, y=r sinθ vale r, lo que explica el factor r. La transformación debe aplicarse al dominio y al integrando además de al elemento diferencial.

Un ejemplo sencillo es x=2u, y=3v sobre el cuadrado 0≤u≤1, 0≤v≤1. Su imagen es el rectángulo 0≤x≤2, 0≤y≤3. La matriz jacobiana tiene entradas diagonales 2 y 3, de modo que |J_T|=6. Así, el área de la imagen es ∫_0^1∫_0^1 6 dv du=6. Omitir el jacobiano produciría erróneamente 1.

Para franjas diagonales puede convenir u=x+y, v=x−y. La transformación inversa es x=(u+v)/2, y=(u−v)/2; su jacobiano tiene valor absoluto 1/2. Si se calcula el jacobiano directo de (x,y) a (u,v), el valor absoluto es 2. Debe elegirse el factor de la transformación que expresa las variables originales en función de las nuevas.

El procedimiento completo tiene cuatro pasos: transformar las fronteras de D, hallar los nuevos límites, reescribir f y multiplicar por |J_T|. Si la transformación no es inyectiva en la región elegida, quizá debamos dividirla antes de usar la fórmula. Esto puede ocurrir con coordenadas angulares cuando el intervalo recorre una vuelta completa y repite puntos de una frontera.

Como control, integrar f=1 debería dar un área razonable. Un jacobiano que se anula en algunos puntos no invalida automáticamente el cambio, pero exige revisar las hipótesis y la geometría del caso concreto.

## 6. Integrales triples y descripción de sólidos

Una integral triple ∫∫∫_E f(x,y,z)dV acumula una función sobre un sólido E del espacio. Puede definirse mediante sumas sobre celdas pequeñas de volumen ΔV. Cuando f=1, devuelve el volumen de E; cuando f es densidad volumétrica en kg/m³, devuelve su masa. Si f cambia de signo, el resultado es una acumulación con signo y no un volumen geométrico.

En una caja rectangular, Fubini permite resolver la integral en distintos órdenes bajo las hipótesis usuales. En un sólido con fronteras curvas, los límites de la variable interior dependen de las otras dos. Una descripción frecuente coloca E entre z=g₁(x,y) y z=g₂(x,y), sobre una región D del plano xy. Se integra primero en z y después sobre D. El dibujo de D es la proyección o «sombra» del sólido sobre el plano.

Por ejemplo, sea E el tetraedro del primer octante limitado por el plano x+y+z=1. La proyección en xy cumple x≥0, y≥0 y x+y≤1. Para cada punto (x,y), la altura z va de 0 a 1−x−y. Su volumen es ∫_0^1∫_0^{1−x}∫_0^{1−x−y}1 dz dy dx=1/6. También se puede comprobar mediante la fórmula geométrica del volumen de un tetraedro con interceptos unitarios en los ejes.

Cuando las superficies superior e inferior se invierten en parte de la proyección, debemos dividir D o corregir los límites. Un resultado negativo al integrar 1 sobre un supuesto sólido revela que los límites o el orden de las fronteras están mal. Si resulta más simple proyectar sobre xz o yz, se puede escoger otro orden de integración.

La integral triple también permite calcular promedios. Si E tiene volumen V>0, el valor promedio de f en E es (1/V)∫∫∫_E f dV. Por ejemplo, para una temperatura T, la integral tiene unidades de temperatura por volumen; al dividir por V queda una temperatura. Las unidades ayudan a distinguir una integral total de su promedio.

Describir correctamente E suele requerir más trabajo que integrar. Es útil fijar dos coordenadas, imaginar un segmento paralelo al tercer eje y preguntar entre qué superficies se mueve. Así se obtiene la integral interior sin adivinar límites.

## 7. Coordenadas cilíndricas

Las coordenadas cilíndricas combinan polares en el plano xy con la altura cartesiana z: x=r cosθ, y=r sinθ y z=z. Son apropiadas para cilindros, paraboloides de revolución y sólidos cuya proyección horizontal es un disco o una corona. El elemento de volumen es dV=r dr dθ dz, en el orden que corresponda a los límites. El factor r proviene de la base polar; z no aporta otro factor.

Dentro del cilindro x²+y²≤4 y entre los planos z=0 y z=3, los límites pueden escribirse como 0≤r≤2, 0≤θ≤2π y 0≤z≤3. El volumen es ∫_0^{2π}∫_0^2∫_0^3 r dz dr dθ=12π, igual al área de la base 4π por la altura 3. Si se integra la función x²+y², el integrando pasa a r² y la integral sobre el mismo sólido es ∫_0^{2π}∫_0^2∫_0^3 r³ dz dr dθ=24π.

Las fronteras verticales pueden depender de r. El sólido bajo el paraboloide z=4−x²−y² y sobre el plano z=0 se describe por 0≤θ≤2π, 0≤r≤2 y 0≤z≤4−r². La intersección con z=0 determina r=2. Al integrar 1 obtenemos ∫_0^{2π}∫_0^2(4−r²)r dr dθ=8π. Cada anillo de radio r tiene altura 4−r².

Una esfera es distinta de un cilindro, aunque su proyección también sea un disco. La esfera x²+y²+z²≤4 se transforma en r²+z²≤4. Para cada r entre 0 y 2, z recorre −√(4−r²)≤z≤√(4−r²). Describirla solo con 0≤r≤2 dejaría sin determinar la altura.

Si un sólido está fuera de un cilindro interior de radio a y dentro de otro de radio b, los límites radiales son a≤r≤b. Si ocupa solo medio espacio angular, θ recorre un intervalo de longitud π. Cada condición del enunciado debe aparecer en los límites o justificarse como consecuencia de las otras.

Las cilíndricas ofrecen una forma natural de relacionar una integral triple con una integral doble sobre la base: al integrar primero en z, aparece la altura del sólido y queda una integral doble en polares.

## 8. Coordenadas esféricas

Las coordenadas esféricas usan la distancia ρ al origen, el ángulo azimutal θ en el plano xy y el ángulo polar φ medido desde el eje z positivo. Nuestra convención es x=ρ sinφ cosθ, y=ρ sinφ sinθ, z=ρ cosφ, con ρ≥0, 0≤θ<2π y 0≤φ≤π. Es importante declarar la convención: algunos libros intercambian los nombres de θ y φ.

El elemento de volumen es dV=ρ² sinφ dρ dφ dθ. Ambos factores pertenecen al jacobiano y no pueden omitirse. La bola de radio R centrada en el origen se describe con 0≤ρ≤R, 0≤φ≤π y 0≤θ≤2π. Su volumen es ∫_0^{2π}∫_0^π∫_0^R ρ² sinφ dρ dφ dθ=4πR³/3.

En el hemisferio superior se impone z≥0, equivalente a 0≤φ≤π/2. Su volumen es la mitad del de la bola completa. Una superficie cónica con vértice en el origen suele describirse con φ constante; por eso la intersección de un cono y una esfera puede dar límites esféricos simples.

Como ejemplo, integremos x²+y²+z² sobre la bola unitaria. La función se convierte en ρ²; al multiplicar por el jacobiano queda ρ⁴ sinφ. El resultado es ∫_0^{2π}∫_0^π∫_0^1 ρ⁴ sinφ dρ dφ dθ=4π/5. Dividiendo por el volumen de la bola, 4π/3, el promedio de la distancia al origen al cuadrado es 3/5.

Para elegir entre cilíndricas y esféricas hay que comparar fronteras e integrando. Las esféricas son cómodas si dominan esferas y conos concéntricos. Las cilíndricas pueden ser mejores si el sólido está entre alturas z=z₁(r) y z=z₂(r). Ningún sistema es superior en todos los ejercicios.

Una comprobación angular evita muchos errores: φ=0 apunta hacia arriba, φ=π/2 corresponde al plano xy y φ=π apunta hacia abajo. Además, ρ no es negativo bajo la convención indicada. Estos detalles determinan qué parte del espacio cubre la integral.

## 9. Masa, centro de masa y momentos

Si una lámina ocupa D y tiene densidad superficial δ(x,y), su masa es M=∫∫_D δ(x,y)dA. Los primeros momentos son M_y=∫∫_D xδ(x,y)dA respecto del eje y y M_x=∫∫_D yδ(x,y)dA respecto del eje x. El centro de masa se obtiene mediante x̄=M_y/M e ȳ=M_x/M, siempre que M>0. El subíndice puede resultar contraintuitivo: la distancia al eje y se mide con x.

Consideremos D=[0,2]×[0,1] y δ(x,y)=1+x. La masa es ∫_0^2∫_0^1(1+x)dy dx=4. El momento respecto del eje y es ∫_0^2∫_0^1 x(1+x)dy dx=2+8/3=14/3. Por tanto, x̄=(14/3)/4=7/6. El centro geométrico está en x=1; la densidad mayor a la derecha desplaza el centro de masa hacia x>1. La densidad no varía con y, así que ȳ=1/2.

Para un sólido E con densidad volumétrica δ(x,y,z), la masa es M=∫∫∫_E δ dV. Sus coordenadas de centro de masa son los promedios ponderados x̄=(1/M)∫∫∫_E xδ dV, ȳ=(1/M)∫∫∫_E yδ dV y z̄=(1/M)∫∫∫_E zδ dV. Si δ es constante, se obtiene el centroide geométrico. La simetría puede anular ciertas coordenadas sin integrar: si E y δ son simétricos respecto del plano yz, entonces x̄=0.

Un momento de inercia pondera más los puntos alejados del eje. Para una lámina, I_z=∫∫_D(x²+y²)δ(x,y)dA es el momento respecto del eje perpendicular al plano que pasa por el origen. En una lámina circular, polares simplifica x²+y² a r² y aporta el factor r del área.

El valor promedio de una función f sobre D es (1/Área(D))∫∫_D f dA. No debe confundirse ese promedio con el centro de masa, que pondera posiciones por densidad y divide por masa. Revisar unidades ayuda: densidad superficial por área da masa; el primer momento tiene unidades de masa multiplicada por longitud y al dividir por masa queda una longitud.

## 10. Método de trabajo y ejercicios integradores

Una integral multivariable comienza con la geometría. Primero se identifica si se integra sobre una lámina o un sólido y se dibujan sus fronteras. Luego se decide si se busca área, volumen, masa o promedio. Después se escogen coordenadas que simplifiquen el dominio y la función. Por último, se escriben límites, integrando y elemento diferencial antes de calcular primitivas.

En cartesianas hay que describir cortes y escoger el orden de integración. En polares y cilíndricas aparece el factor r; en esféricas aparece ρ² sinφ; en un cambio general aparece el valor absoluto del jacobiano. Una comprobación con f=1 permite revisar si los límites dan un área o volumen plausible. Al finalizar se verifican el signo, las unidades y el tamaño aproximado del resultado.

**Ejercicio 1: cambio de orden.** Sea D la región 0≤x≤1, x²≤y≤x. Calcula ∫∫_D y dA con ambos órdenes. Con dy primero se obtiene ∫_0^1∫_{x²}^x y dy dx=∫_0^1(x²−x⁴)/2 dx=1/15. Con dx primero se usa 0≤y≤1, y≤x≤√y. Ambas expresiones deben coincidir.

**Ejercicio 2: promedio en un disco.** Calcula el promedio de f(x,y)=x²+y² sobre el disco unitario. La integral vale π/2 y el área del disco es π, de modo que el promedio es 1/2. Explica por qué es menor que el máximo 1 que la función toma en la frontera.

**Ejercicio 3: paraboloide.** Halla el volumen bajo z=9−x²−y² y sobre z=0. En cilíndricas, 0≤r≤3, 0≤θ≤2π y 0≤z≤9−r². La integral ∫_0^{2π}∫_0^3∫_0^{9−r²}r dz dr dθ vale 81π/2. Es menor que el volumen 81π del cilindro de radio 3 y altura 9.

**Ejercicio 4: esfera uniforme.** En una bola de radio R con densidad constante δ₀, calcula la masa con coordenadas esféricas y localiza el centro de masa por simetría. La masa es δ₀·4πR³/3 y el centro está en el origen. Si la densidad dependiera de z, habría que revisar esa simetría.

**Ejercicio 5: transformación lineal.** Sea S el cuadrado 0≤u≤1, 0≤v≤1 y T(u,v)=(u+v,u−v). Describe la imagen D y calcula su área mediante el jacobiano. El determinante de T vale −2, así que el área es 2. Los vértices transformados son (0,0), (1,1), (1,−1) y (2,0).

Estos ejercicios muestran relaciones de aprendizaje: la integral definida ayuda a interpretar la integral doble; Fubini permite calcularla; describir regiones permite usar límites variables; el jacobiano fundamenta los cambios de coordenadas; y esas herramientas permiten calcular masas, promedios y centros de masa. La secuencia es una guía para estudiar este apunte, no una regla universal de todas las asignaturas.
